FROM ubuntu:18.04

RUN apt-get update && apt-get upgrade -y && apt-get install -y \
    git \
    wget

# clone code repository
RUN git clone https://github.com/Urban-Analytics/RAMP-UA.git /app/RAMP-UA

# install miniconda
RUN wget -q https://repo.continuum.io/miniconda/Miniconda3-latest-Linux-x86_64.sh -O miniconda.sh 
RUN chmod +x miniconda.sh 
RUN ./miniconda.sh -b -p /opt/miniconda 

ENV PATH="/opt/miniconda/bin:${PATH}"

RUN conda env create -f /app/RAMP-UA/environment.yml \
    && source activate ramp-ua \
    && cd /app/RAMP-UA \
    && python setup.py install

CMD python microsim/main.py
