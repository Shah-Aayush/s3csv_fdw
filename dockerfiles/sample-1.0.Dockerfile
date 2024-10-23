# Use the official PostgreSQL image as a base
FROM postgres:13

# Install dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    git \
    && rm -rf /var/lib/apt/lists/*

# Clone the repository
RUN git clone --branch enhanced https://github.com/Shah-Aayush/hydradatabase-s3csv_fdw.git /usr/src/hydradatabase-s3csv_fdw

# Set the working directory
WORKDIR /usr/src/hydradatabase-s3csv_fdw

# Install the extension
RUN python3 setup.py install

# Set the default command to run PostgreSQL
CMD ["postgres"]