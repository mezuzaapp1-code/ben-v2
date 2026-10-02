"""Private S3 staging, isolated by execution. Lifecycle is required for crash cleanup."""
import hashlib
import os
import uuid


class ShortStaging:
    def __init__(self, client=None):
        self.client = client
        self.bucket = os.getenv('BEN_SHORT_STAGE_BUCKET', '')

    def prepare(self, execution, assets):
        try:
            return self._prepare(execution, assets)
        except Exception:
            # SDK exceptions may contain endpoints or credentials. Never persist them.
            raise ValueError('short_staging_failed') from None

    def _prepare(self, execution, assets):
        if not self.bucket:
            raise ValueError('short_staging_unconfigured')
        if self.client is None:
            import boto3
            from botocore.config import Config
            self.client = boto3.client('s3', config=Config(signature_version='s3v4',
                connect_timeout=5, read_timeout=15, retries={'total_max_attempts': 2}))
        block = self.client.get_public_access_block(Bucket=self.bucket)['PublicAccessBlockConfiguration']
        if not all(block.get(k) for k in ('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets')):
            raise ValueError('short_staging_not_private')
        rules = self.client.get_bucket_lifecycle_configuration(Bucket=self.bucket)['Rules']
        if not any(r.get('Status') == 'Enabled' and r.get('Filter') == {'Prefix': 'ben-short/'}
                   and r.get('Expiration', {}).get('Days') == 1 for r in rules):
            raise ValueError('short_staging_cleanup_required')
        urls = []
        for i, (data, mime) in enumerate(assets):
            key = f'ben-short/{uuid.UUID(str(execution))}/{i}-{hashlib.sha256(data).hexdigest()}'
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=mime,
                                   ServerSideEncryption='AES256')
            urls.append(self.client.generate_presigned_url('get_object',
                Params={'Bucket': self.bucket, 'Key': key}, ExpiresIn=1800))
        return urls
