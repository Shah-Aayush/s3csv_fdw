"""
An Amazon S3 Foreign Data Wrapper

"""
from multicorn import ForeignDataWrapper
from multicorn.utils import log_to_postgres, ERROR, WARNING, DEBUG
import boto3
import csv
from io import BytesIO, TextIOWrapper
from botocore.client import Config
from datetime import datetime
import random


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
        - generate_bad_file: Whether to generate a .bad file for corrupted rows (default: true)
        - truncstring: Whether to truncate string values based on column definition (default: false)
        - lfinstring: Whether to handle unescaped linefeed in the row (default: false)
        - ctrlchars: Whether to escape special characters in the string (default: false)
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
        
        self.generate_bad_file = self.parse_bool_option(fdw_options.get("generate_bad_file", "true"))
        self.truncstring = self.parse_bool_option(fdw_options.get("truncstring", "false"))
        self.lfinstring = self.parse_bool_option(fdw_options.get("lfinstring", "false"))
        self.ctrlchars = self.parse_bool_option(fdw_options.get("ctrlchars", "false"))
        
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
            bad_rows = []  # Store bad rows if generate_bad_file is true
            
            for line in reader:
                if count >= self.skip_header:
                    if not checked:
                        checked = True
                       

                    try:
                        self.validate_columns(line)
                        # Process the row and yield it
                        processed_row = self.process_row(line)
                        yield processed_row
                    except Exception as e:
                        # Log the specific error for the row
                        log_to_postgres(f"Error processing row: {line} -> {str(e)}", WARNING)
                        
                        # Add the corrupted row to bad rows if generation is enabled
                        if self.generate_bad_file:
                            bad_rows.append(line)

                count += 1

            # Handle bad rows
            if self.generate_bad_file and bad_rows:
                self.write_bad_file(bad_rows)

        except Exception as e:
            log_to_postgres(f"Error reading CSV data: {str(e)}", ERROR)
            raise

    def process_row(self, row):
        """Process a row according to FDW options (truncstring, lfinstring, ctrlchars)."""
        processed_row = []
        columns = list(self.columns.items())  # Get column definitions once

        for idx, (col_name, col_def) in enumerate(columns):
            try:
                value = row[idx] if idx < len(row) else None  # Handle missing columns gracefully
                col_name = list(self.columns.keys())[idx]
                col_def = self.columns[col_name]

                # Extract type_name and type_modifier from col_def safely
                type_name = col_def.type_name if hasattr(col_def, 'type_name') else None

                # Extract type_modifier from type_name, if applicable
                type_modifier = -1
                if type_name and '(' in type_name and ')' in type_name:
                    try:
                        type_modifier = int(type_name.split('(')[1].split(')')[0])
                    except ValueError:
                        type_modifier = -1  # Fallback value if parsing fails

                # Process the value if not None
                if value is not None:
                    # Handle line feed string
                    if self.lfinstring and '\n' in value:
                        value = value.replace('\n', '\\n')

                    # Handle control characters
                    if self.ctrlchars:
                        value = value.replace('\t', '\\t').replace('\r', '\\r').replace('\n', '\\n')

                    # Apply truncation logic for string types
                    if type_name and ('character varying' in type_name or 'text' in type_name):
                        if type_modifier > 0 and self.truncstring and len(value) > type_modifier:
                            value = value[:type_modifier]  # Truncate value

                    # Handle empty string for non-VARCHAR columns by converting to None (NULL)
                    if value == "" and col_def.type_name not in ('character varying', 'text'):
                        value = None

                # Add processed value to the row
                processed_row.append(value)

            except Exception as e:
                # Log detailed error and re-raise for higher-level handling
                log_to_postgres(f"Error processing column {idx} ({col_name}) for row {row}: {str(e)}", WARNING)
                raise

        return processed_row







    def write_bad_file(self, bad_rows):
        """Write bad rows to a .bad file and upload it to S3."""
        # Generate a timestamp for the bad file name
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]  # Format: YYYYMMDDHHMMSSmmm
        random_part = f"{random.randint(10000000, 99999999)}"  # Generate random part
        bad_filename = (
            f"{self.filename}.{timestamp}.{random_part}.bad"
        )

        bad_stream = BytesIO()
        wrapper = TextIOWrapper(bad_stream, encoding='utf-8')
        writer = csv.writer(
            wrapper,
            delimiter=self.delimiter,
            quotechar=self.quotechar,
            quoting=csv.QUOTE_ALL  # Ensure all values are quoted
        )
        writer.writerows(bad_rows)
        wrapper.flush()  # Ensure all data is written to the BytesIO stream
        bad_stream.seek(0)  # Rewind the stream to the beginning for uploading

        try:
            s3 = self.get_s3_client()
            s3.upload_fileobj(bad_stream, self.bucket, bad_filename)
            log_to_postgres(f"Bad rows written to {bad_filename} and uploaded to S3", WARNING)
        except Exception as e:
            log_to_postgres(f"Failed to upload bad file {bad_filename} to S3: {str(e)}", ERROR)
        finally:
            wrapper.close()  # Properly close the wrapper to release resources

    def validate_columns(self, line):
        """
        Validate CSV columns against the table definition.

        :param line: A list representing a row in the CSV file.
        :raises Exception: If the row does not match the table column count.
        """
        if len(line) != len(self.columns):
            log_message = (
                f"Corrupted row: Expected {len(self.columns)} columns, "
                f"but got {len(line)}. Row content: {line}"
            )
            log_to_postgres(log_message, WARNING)
            raise Exception(log_message)

