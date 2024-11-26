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
import re
import string
import traceback



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

    Additional parsing options:
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
        - trunc_col: Truncate column values based on column definition
        - lfinstring: Handle unescaped linefeeds in rows
        - ctrlchars: Escape special characters in varchar fields
    """

    def __init__(self, fdw_options, fdw_columns):
        super(S3Fdw, self).__init__(fdw_options, fdw_columns)

        # Existing initialization code...
        
        # New parsing options
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
        self.trunc_col = self.parse_bool_option(fdw_options.get("trunc_col", "false"))
        self.lfinstring = self.parse_bool_option(fdw_options.get("lfinstring", "false"))
        self.ctrlchars = self.parse_bool_option(fdw_options.get("ctrlchars", "false"))

        self.columns = fdw_columns
        self.column_info = {}
        for col_name, col_def in fdw_columns.items():
            # Store column type and length information
            type_info = col_def.get('type_name', '').lower()
            
            # Extract max length for string-like types
            max_length = None
            if 'varchar' in type_info or 'char' in type_info:
                # Extract length from type definition
                match = re.search(r'\((\d+)\)', str(col_def))
                if match:
                    max_length = int(match.group(1))
            
            self.column_info[col_name] = {
                'type': type_info,
                'max_length': max_length,
                # Flag to indicate if column is critical (cannot be null)
                'required': col_def.get('not_null', False)
            }

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
            s3.download_fileobj(self.bucket, self.filename, stream)
            
            stream.seek(0)
            reader = csv.reader(
                TextIOWrapper(stream, encoding='utf-8', errors='replace'),
                delimiter=self.delimiter,
                quotechar=self.quotechar
            )

            count = 0
            bad_rows = []
            
            for line in reader:
                if count >= self.skip_header:
                    # Capture full error details
                    row_error = None
                    processed_row = []

                    # Ensure we have enough columns
                    if len(line) < len(self.columns):
                        row_error = f"Insufficient columns. Expected {len(self.columns)}, got {len(line)}"
                        if self.generate_bad_file:
                            bad_rows.append(line)
                        continue

                    try:
                        # Process each column
                        for value, col_name in zip(line, self.columns):
                            cleaned_value = self.clean_and_validate_value(value, col_name)
                            processed_row.append(cleaned_value)
                        
                        # If we get here, row is fully processed
                        yield processed_row

                    except Exception as e:
                        # Capture detailed error information
                         if self.generate_bad_file:
                            bad_rows.append(line)

                count += 1

            # Handle bad rows - write to detailed bad file
            if self.generate_bad_file and bad_rows:
                self.write_bad_file(bad_rows)

        except Exception as e:
            log_to_postgres(f"Critical error reading CSV data: {str(e)}", ERROR)
            raise


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
            log_to_postgres(f"Bad rows written to {bad_filename} and uploaded to S3", DEBUG)
        except Exception as e:
            log_to_postgres(f"Failed to upload .bad file to S3: {str(e)}", ERROR)
        finally:
            wrapper.close()  # Properly close the wrapper to release resources


    def validate_columns(self, line):
        """Validate CSV columns against table definition"""
        if len(line) > len(self.columns):
            log_to_postgres("CSV file has more columns than defined in the table", WARNING)
        if len(line) < len(self.columns):
            log_to_postgres("CSV file has fewer columns than defined in the table", WARNING)

    def process_column_value(self, value, column_name):
            """Process individual column value based on parsing options"""
            # Truncate column if enabled and column has a max length
            if self.trunc_col:
                max_length = self.column_lengths.get(column_name, float('inf'))
                value = value[:max_length] if value else value

            # Handle unescaped linefeeds
            if self.lfinstring and isinstance(value, str):
                # Replace unescaped linefeeds
                value = value.replace('\n', '\\n')

            # Escape control characters
            if self.ctrlchars and isinstance(value, str):
                # Escape special control characters
                value = re.sub(r'[\x00-\x1F\x7F]', lambda m: f'\\{ord(m.group(0)):03o}', value)

            return value
    def validate_and_convert_value(self, value, col_name):
            """
            Validate and convert value based on column type
            """
            col_type = self.column_info[col_name]['type']
            max_length = self.column_info[col_name]['max_length']

            try:
                # Type and length validation
                if 'varchar' in col_type or 'char' in col_type:
                    # String type validation
                    if max_length and len(value) > max_length:
                        log_to_postgres(
                            f"Value for {col_name} exceeds max length {max_length}", 
                            WARNING
                        )
                        value = value[:max_length]
                
                elif 'int' in col_type:
                    # Integer type conversion
                    value = int(value)
                
                elif 'numeric' in col_type or 'decimal' in col_type:
                    # Numeric type conversion
                    value = float(value)
                
                elif 'date' in col_type:
                    # Date type conversion
                    value = datetime.strptime(value, '%Y-%m-%d').date()
                
                # Add more type-specific conversions as needed
                
                return value
            
            except ValueError as e:
                log_to_postgres(
                    f"Type conversion error for column {col_name}: {str(e)}", 
                    WARNING
                )
                return None
    def clean_and_validate_value(self, value, col_name):
        """
        Comprehensive value cleaning and validation
        """
        # Handle None or empty value
        if value is None or value == '':
            # Check if column is required
            if self.column_info[col_name].get('required', False):
                raise ValueError(f"Required column {col_name} cannot be null")
            return None

        # Remove any leading/trailing whitespace and newlines
        value = str(value).strip()

        # If still empty after stripping, return None
        if not value:
            if self.column_info[col_name].get('required', False):
                raise ValueError(f"Required column {col_name} cannot be empty")
            return None

        col_type = self.column_info[col_name]['type']
        max_length = self.column_info[col_name]['max_length']

        try:
            # Handle different type conversions
            if 'int' in col_type or 'bigint' in col_type:
                # Remove any non-numeric characters except minus sign
                value = re.sub(r'[^\-0-9]', '', value)
                # Convert to integer
                if not value:
                    raise ValueError(f"Invalid integer value for {col_name}")
                converted_value = int(value)
            
            elif 'numeric' in col_type or 'decimal' in col_type or 'float' in col_type:
                # Remove any non-numeric characters except decimal point and minus sign
                value = re.sub(r'[^\-0-9.]', '', value)
                # Convert to float
                if not value:
                    raise ValueError(f"Invalid numeric value for {col_name}")
                converted_value = float(value)
            
            elif 'varchar' in col_type or 'char' in col_type or 'text' in col_type:
                # Truncate if necessary
                if max_length:
                    value = value[:max_length]
                
                # Remove non-printable characters
                converted_value = ''.join(
                    char for char in value 
                    if char in string.printable
                )
            
            elif 'date' in col_type:
                # Attempt various date parsing
                date_formats = [
                    '%Y-%m-%d', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d', 
                    '%d/%m/%Y', '%m-%d-%Y', '%Y-%m-%dT%H:%M:%S', 
                    '%Y-%m-%d %H:%M:%S'
                ]
                
                for fmt in date_formats:
                    try:
                        converted_value = datetime.strptime(value, fmt).date()
                        break
                    except ValueError:
                        continue
                else:
                    raise ValueError(f"Unable to parse date for {col_name}")
            
            else:
                # Default: keep as string
                converted_value = value

            return converted_value

        except Exception as e:
            # Wrap and re-raise with more context
            raise ValueError(f"Error converting {col_name}: {str(e)}")