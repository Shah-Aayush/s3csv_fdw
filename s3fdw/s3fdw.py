"""
An Amazon S3 Foreign Data Wrapper

"""
from multicorn import ForeignDataWrapper
from multicorn.utils import log_to_postgres, ERROR, WARNING, DEBUG
import boto3
import csv
from io import BytesIO, TextIOWrapper
from botocore.client import Config


# In at least some cases, bucket names are required to follow subdomain.domain
# format.
# Per https://docs.aws.amazon.com/AmazonS3/latest/dev/BucketRestrictions.html
# Amazon recommends handling this by using custom TLS domain validation logic.
#
# Here we do so using a snippet posted by @ykhrustalev on
# https://github.com/boto/boto/issues/2836
import ssl

_old_match_hostname = ssl.match_hostname

def remove_dot(host):
    """
    >>> remove_dot('a.x.s3-eu-west-1.amazonaws.com')
    'ax.s3-eu-west-1.amazonaws.com'
    >>> remove_dot('a.s3-eu-west-1.amazonaws.com')
    'a.s3-eu-west-1.amazonaws.com'
    >>> remove_dot('s3-eu-west-1.amazonaws.com')
    's3-eu-west-1.amazonaws.com'
    >>> remove_dot('a.x.s3-eu-west-1.example.com')
    'a.x.s3-eu-west-1.example.com'
    """
    if not host.endswith('.amazonaws.com'):
        return host
    parts = host.split('.')
    h = ''.join(parts[:-3])
    if h:
        h += '.'
    return h + '.'.join(parts[-3:])


def _new_match_hostname(cert, hostname):
    return _old_match_hostname(cert, remove_dot(hostname))


ssl.match_hostname = _new_match_hostname

class S3Fdw(ForeignDataWrapper):
    """A foreign data wrapper for accessing csv files from S3 or S3-compatible storage.

    Valid options:
        - aws_access_key: AWS access key
        - aws_secret_key: AWS secret key
        - bucket: S3 bucket name
        - filename: path to the CSV file
        - endpoint: Custom S3 endpoint URL (optional)
        - region: AWS region (optional)
        - verify_ssl: Verify SSL certificate (default: true)
        - signature_version: S3 signature version (default: s3v4)
        - addressing_style: S3 addressing style (path or virtual)
        - delimiter: CSV delimiter (default: ",")
        - quotechar: CSV quote character (default: '"')
        - skip_header: Number of lines to skip, or boolean
    """

    def __init__(self, fdw_options, fdw_columns):
        super(S3Fdw, self).__init__(fdw_options, fdw_columns)

        # Required options
        self.validate_required_options(fdw_options)
        
        # S3 configuration
        self.aws_access_key = fdw_options["aws_access_key"]
        self.aws_secret_key = fdw_options["aws_secret_key"]
        self.bucket = fdw_options.get('bucket', fdw_options.get('bucketname'))
        self.filename = fdw_options["filename"]
        
        # S3 endpoint configuration
        self.endpoint = fdw_options.get("endpoint")
        self.region = fdw_options.get("region", "")
        self.verify_ssl = self.parse_bool_option(fdw_options.get("verify_ssl", "true"))
        self.signature_version = fdw_options.get("signature_version", "s3v4")
        self.addressing_style = fdw_options.get("addressing_style", "path")

        # CSV configuration
        self.delimiter = fdw_options.get("delimiter", ",")
        self.quotechar = fdw_options.get("quotechar", fdw_options.get("quote", '"'))
        self.skip_header = self.parse_header_option(fdw_options)
        
        self.columns = fdw_columns

    def validate_required_options(self, options):
        """Validate required FDW options"""
        required = ["aws_access_key", "aws_secret_key", "bucket", "filename"]
        for opt in required:
            if not options.get(opt):
                log_to_postgres(f"Missing required option: {opt}", ERROR)

    def parse_bool_option(self, value):
        """Parse boolean option values"""
        if isinstance(value, bool):
            return value
        return value.lower() in ('true', 't', 'yes', 'y', '1')

    def parse_header_option(self, options):
        """Parse header skip option"""
        skip_header = options.get('skip_header')
        if skip_header is not None:
            return int(skip_header)
        
        header = options.get('header')
        if header is not None:
            return 1 if self.parse_bool_option(header) else 0
        return 0

    def get_s3_client(self):
        """Create S3 client with proper configuration"""
        try:
            config = Config(
                signature_version=self.signature_version,
                s3={
                    'addressing_style': self.addressing_style
                }
            )
            
            client_kwargs = {
                'aws_access_key_id': self.aws_access_key,
                'aws_secret_access_key': self.aws_secret_key,
                'config': config
            }

            # Add optional configurations
            if self.endpoint:
                client_kwargs['endpoint_url'] = self.endpoint
            if self.region:
                client_kwargs['region_name'] = self.region
            if not self.verify_ssl:
                client_kwargs['verify'] = False

            return boto3.client('s3', **client_kwargs)
            
        except Exception as e:
            log_to_postgres(f"Failed to create S3 client: {str(e)}", ERROR)
            raise

    def execute(self, quals, columns):
        try:
            s3 = self.get_s3_client()
            
            stream = BytesIO()
            try:
                s3.download_fileobj(self.bucket, self.filename, stream)
            except Exception as e:
                log_to_postgres(f"Failed to download file {self.filename} from bucket {self.bucket}: {str(e)}", ERROR)
                raise
            
            stream.seek(0)
            reader = csv.reader(
                TextIOWrapper(stream, encoding='utf-8'),
                delimiter=self.delimiter,
                quotechar=self.quotechar
            )

            count = 0
            checked = False
            
            for line in reader:
                if count >= self.skip_header:
                    if not checked:
                        checked = True
                        self.validate_columns(line)
                    
                    row = line[:len(self.columns)]
                    nulled_row = [v if v else None for v in row]
                    yield nulled_row
                count += 1

        except Exception as e:
            log_to_postgres(f"Error reading CSV data: {str(e)}", ERROR)
            raise

    def validate_columns(self, line):
        """Validate CSV columns against table definition"""
        if len(line) > len(self.columns):
            log_to_postgres("CSV file has more columns than defined in the table", WARNING)
        if len(line) < len(self.columns):
            log_to_postgres("CSV file has fewer columns than defined in the table", WARNING)