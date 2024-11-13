# Use the official PostgreSQL image as a base
FROM ghcr.io/hydradatabase/hydra:15

# Install dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    git \
    postgresql-server-dev-13 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Clone the multicorn repository
RUN git clone https://github.com/Kozea/Multicorn.git /tmp/multicorn

# Build and install the multicorn extension
RUN cd /tmp/multicorn && \
    make && \
    make install

# Clone the repository
RUN git clone --branch master https://github.com/Shah-Aayush/s3csv_fdw.git /tmp/s3fdw

# Set the working directory
WORKDIR /tmp/s3fdw

# Install S3 FDW
RUN pip3 install --break-system-packages -e .

# Clean up
RUN apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Set the default command to run PostgreSQL
CMD ["postgres"]