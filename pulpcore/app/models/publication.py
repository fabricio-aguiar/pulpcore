from django.db import models, transaction

from . import storage
from .base import MasterModel, Model
from .repository import Remote, Repository, RepositoryVersion
from .task import CreatedResource


class Publication(MasterModel):
    """
    A publication contains metadata and artifacts associated with content
    contained within a RepositoryVersion.

    Using as a context manager is highly encouraged.  On context exit, the complete attribute
    is set True provided that an exception has not been raised.  In the event and exception
    has been raised, the publication is deleted.

    Fields:
        complete (models.BooleanField): State tracking; for internal use. Indexed.
        pass_through (models.BooleanField): Indicates that the publication is a pass-through
            to the repository version. Enabling pass-through has the same effect as creating
            a PublishedArtifact for all of the content (artifacts) in the repository.

    Relations:
        publisher (models.ForeignKey): The publisher that created the publication.
        repository_version (models.ForeignKey): The RepositoryVersion used to
            create this Publication.

    Examples:
        >>> publisher = ...
        >>> repository_version = ...
        >>>
        >>> with Publication.create(repository_version, publisher) as publication:
        >>>     for content in repository_version.content():
        >>>         for content_artifact in content.contentartifact_set.all():
        >>>             artifact = PublishedArtifact(...)
        >>>             artifact.save()
        >>>             metadata = PublishedMetadata(...)
        >>>             metadata.save()
        >>>             ...
        >>>
    """
    TYPE = 'publication'

    complete = models.BooleanField(db_index=True, default=False)
    pass_through = models.BooleanField(default=False)

    publisher = models.ForeignKey('Publisher', on_delete=models.CASCADE, null=True)
    repository_version = models.ForeignKey('RepositoryVersion', on_delete=models.CASCADE)

    @classmethod
    def create(cls, repository_version, publisher=None, pass_through=False):
        """
        Create a publication.

        This should be used to create a publication.  Using Publication() directly
        is highly discouraged.

        Args:
            repository_version (pulpcore.app.models.RepositoryVersion): The repository
                version to be published.
            publisher (pulpcore.app.models.Publisher): The publisher used
                to create the publication.
            pass_through (bool): Indicates that the publication is a pass-through
                to the repository version. Enabling pass-through has the same effect
                as creating a PublishedArtifact for all of the content (artifacts)
                in the repository.

        Returns:
            pulpcore.app.models.Publication: A created Publication in an incomplete state.

        Notes:
            Adds a Task.created_resource for the publication.
        """
        with transaction.atomic():
            publication = cls(
                pass_through=pass_through,
                repository_version=repository_version)
            if publisher:
                publication.publisher = publisher
            publication.save()
            resource = CreatedResource(content_object=publication)
            resource.save()
            return publication

    @property
    def repository(self):
        """
        Return the associated repository

        Returns:
            pulpcore.app.models.Repository: The repository associated to this publication
        """
        return self.repository_version.repository

    def delete(self, **kwargs):
        """
        Delete the publication.

        Args:
            **kwargs (dict): Delete options.

        Notes:
            Deletes the Task.created_resource when complete is False.
        """
        with transaction.atomic():
            CreatedResource.objects.filter(object_id=self.pk).delete()
            super().delete(**kwargs)

    def __enter__(self):
        """
        Enter context.

        Returns:
            Publication: self
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Set the complete=True, create the publication.

        Args:
            exc_type (Type): (optional) Type of exception raised.
            exc_val (Exception): (optional) Instance of exception raised.
            exc_tb (types.TracebackType): (optional) stack trace.
        """
        if not exc_val:
            self.complete = True
            self.save()
        else:
            self.delete()


class PublishedFile(Model):
    """
    A file included in Publication.

    Fields:
        relative_path (models.CharField): The (relative) path component of the published url.

    Relations:
        publication (models.ForeignKey): The publication in which the artifact is included.

    """
    relative_path = models.CharField(max_length=255)

    publication = models.ForeignKey(Publication, on_delete=models.CASCADE)

    class Meta:
        abstract = True


class PublishedArtifact(PublishedFile):
    """
    An artifact that is part of a publication.

    Relations:
        content_artifact (models.ForeignKey): The referenced content artifact.
    """
    content_artifact = models.ForeignKey('ContentArtifact', on_delete=models.CASCADE)

    class Meta:
        default_related_name = 'published_artifact'
        unique_together = (
            ('publication', 'content_artifact'),
            ('publication', 'relative_path')
        )


class PublishedMetadata(PublishedFile):
    """
    Metadata file that is part of a publication.

    Fields:
        file (models.FileField): The stored file.
    """

    def _storage_path(self, name):
        return storage.published_metadata_path(self, name)

    file = models.FileField(upload_to=_storage_path, max_length=255)

    class Meta:
        default_related_name = 'published_metadata'
        unique_together = (
            ('publication', 'file'),
            ('publication', 'relative_path')
        )


class ContentGuard(MasterModel):
    """
    Defines a named content guard.

    Fields:
        name (models.CharField): Unique guard name.
        description (models.TextField): An optional description.

    """
    name = models.CharField(max_length=255, db_index=True, unique=True)
    description = models.TextField(null=True)


class BaseDistribution(MasterModel):
    """
    A distribution defines how a publication is distributed by the Content App.

    This abstract model can be used by plugin writers to create concrete distributions that are
    stored in separate tables from the Distributions provided by pulpcore.

    The `name` must be unique.

    The ``base_path`` must have no overlapping components. So if a Distribution with ``base_path``
    of ``a/path/foo`` existed, you could not make a second Distribution with a ``base_path`` of
    ``a/path`` or ``a`` because both are subpaths of ``a/path/foo``.

    Fields:
        name (models.CharField): The name of the distribution. Examples: "rawhide" and "stable".
        base_path (models.CharField): The base (relative) path component of the published url.

    Relations:
        content_guard (models.ForeignKey): An optional content-guard.
        remote (models.ForeignKey): A remote that the content app can use to find content not
            yet stored in Pulp.
    """

    name = models.CharField(max_length=255, db_index=True, unique=True)
    base_path = models.CharField(max_length=255, unique=True)

    content_guard = models.ForeignKey(ContentGuard, null=True, on_delete=models.SET_NULL)
    remote = models.ForeignKey(Remote, null=True, on_delete=models.SET_NULL)


class PublicationDistribution(BaseDistribution):
    """
    Define how Pulp's content app will serve a Publication.

    Relations:
        publication (models.ForeignKey): Publication to be served.
    """

    publication = models.ForeignKey(Publication, null=True, on_delete=models.SET_NULL)

    class Meta:
        abstract = True


class RepositoryVersionDistribution(BaseDistribution):
    """
    Define how Pulp's content app will serve a RepositoryVersion or Repository.

    The ``repository`` and ``repository_version`` fields cannot be used together.

    Relations:
        repository (models.ForeignKey): The latest RepositoryVersion for this Repository will be
            served.
        repository_version (models.ForeignKey): RepositoryVersion to be served.
    """

    repository = models.ForeignKey(Repository, null=True, on_delete=models.SET_NULL)
    repository_version = models.ForeignKey(RepositoryVersion, null=True, on_delete=models.SET_NULL)

    class Meta:
        abstract = True
