"""Create edits only from checksum-verified, owned BEN video bytes."""
import asyncio
import hashlib
import uuid

from services.media.edit_repository import EditRepository, error, validated


async def read_video(media_repository, org, user, resource):
    # Reuse BEN's canonical path/checksum/ownership reader. This method never
    # invokes create(), tick(), an adapter or a paid media operation.
    from services.media.service import MediaService
    return await MediaService(repository=media_repository).resource_bytes(org, user, resource)


class EditService:
    def __init__(self, repository=None, reader=read_video):
        self.repo = repository or EditRepository()
        self.reader = reader

    async def create(self, org, user, key, document):
        document = validated(document, key)
        resource = uuid.UUID(document['source']['resource_id'])
        async with self.repo.transaction(org, user) as session:
            await self.repo.source(session, org, user, resource)
        data = await self.reader(self.repo.media, org, user, resource)
        from services.media.local_composer import video_duration
        try:
            duration = float(await asyncio.to_thread(video_duration, data))
        except (ValueError, OSError):
            raise error(422, 'EDIT_SOURCE_UNSUPPORTED', 'A valid BEN video of up to 30 seconds is required') from None
        return await self.repo.create(org, user, key, document,
                                      checksum=hashlib.sha256(data).hexdigest(), duration=duration)
