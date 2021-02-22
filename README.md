![python-testing](https://github.com/Urban-Analytics/RAMP-UA/workflows/python-testing/badge.svg)
[![codecov](https://codecov.io/gh/Urban-Analytics/RAMP-UA/branch/master/graph/badge.svg)](https://codecov.io/gh/Urban-Analytics/RAMP-UA)
# RAMP-UA

This is the code repository for the RAMP Urban Analytics project.

This project contains two implementations of a microsim model which runs on a synthetic population:
1. Python / R implementation, found in [microsim/microsim_model.py](./microsim/microsim_model.py)
2. High performance OpenCL implementation, which can run on both CPU and GPU, 
which is found in the [microsim/opencl](./microsim/opencl) folder. 

Further documentation on the OpenCL model can be found at [microsim/opencl/doc](./microsim/opencl/doc)

Both models should be logically equivalent (with some minor differences). 

## Environment setup

**NB:** The OpenCL model requires following additional installation instructions located in the 
[OpenCL Readme](./microsim/opencl/README.md)

This project currently supports running on Linux and macOS.

To start working with this repository you need to clone it onto your local machine:

```bash
$ git clone https://github.com/Urban-Analytics/RAMP-UA.git
$ cd RAMP-UA
```

This project requires a specific conda environment in order to run so you will need the [conda package manager system](https://docs.anaconda.com/anaconda/install/) installed. Once conda has been installed you can create an environment for this project using the provided environment file.

```bash
$ conda env create -f environment.yml
```

To retrieve data to run the mode you will need to use [Git Large File Storage](https://git-lfs.github.com/) to download the input data. Git-lfs is installed within the conda environment (you may need to run `git lfs install` on your first use of git lfs). To retrieve the data you run the following commands within the root of the project repository:

```bash
$ git lfs fetch
$ git lfs checkout
``` 

Next we install the RAMP-UA package into the environment using `setup.py`:

```bash
# if developing the code base use:
$ python setup.py develop
# for using the code base use
$ python setup.py install
```

### Running the models
Both models can be run from the [microsim/main.py](./microsim/main.py) script, which can be configured with various arguments
to choose which model implementation to run.

#### Python / R model

The Python / R model runs by default, so simply run the main script with no arguments.

```bash
$ python microsim/main.py 
```

#### OpenCL model
To run the OpenCL model pass the `--opencl` flag to the main script, as below.

The OpenCL model runs in "headless" mode by default, however it can also be run with an interactive GUI and visualisation,
to run with the GUI pass the `--opencl-gui` flag, as below.

Run Headless
```bash
$ python microsim/main.py --opencl
```

Run with GUI
```bash
$ python microsim/main.py --opencl-gui
```

#### Caching of population initialisation
The population initialisation step runs before either of the models and can be time consuming (~10 minutes). In order to run
the models using a cache of previous results simply pass the `--use-cache` flag.

### Output Dashboards
Outputs are currently written to the [devon_data/output](./devon_data/output) directory.

Interactive HTML dashboards can be created using the Bokeh library.
 
Run the command below to generate the full dashboard for the Python / R model output, which should automatically open
the HTML file when it finishes.
 ```bash
$ python microsim/dashboard.py
```
Configuration YAML files for the dashboard are located in the [model_parameters](./model_parameters) folder.

The OpenCL model has a more limited dashboard (this may be extended soon), which can be run as follows:
 ```bash
$ python microsim/opencl/ramp/opencl_dashboard.py
```

## Docker

A [Docker](https://www.docker.com/) container is available for this project. This allows you to pull a Docker image of the RAMP-UA project in an uninitialised state from which you can run the `microsim/main.py` script and pass it additional options. The first time the container runs it will fetch the default data source into the container which will increase the total disk space it uses.

```bash
# build the container
$ git clone Urban-Analytics/RAMP-UA

$ cd RAMP-UA

$ docker build . -t rampua:latest

# run the python model
$ docker run -d rampua:latest 

# run the openCL CPU model 
$ docker run -d rampua:latest -ocl
```

### Egress data from container

There are two choices to get output data from the container.

#### 1. Mount the RAMP-UA repository within the Docker container

This step allows you to run the container and generate outputs in one step rather than option 2 which requires you to run an additional docker command to copy data out of the container.

To start, you'll to have the container image available on your machine and have cloned the RAMP-UA repository on your host machine.

```bash
# first check we have the rampua image locally
$ docker images 
REPOSITORY    TAG       IMAGE ID       CREATED         SIZE
rampua        latest    aa3195bdfe3d   3 days ago      5.12GB

# navigate into the RAMP-UA repository locally
$ cd RAMP-UA

# run the container and mount a volume from the host machine RAMP-UA directory
# to the RAMP-UA directory in the container
$ docker run -v /home/user/Code/RAMP-UA/:/app/RAMP-UA/ rampua:latest -ocl
```

Once this runs you will find the output directory within `devon_data` containing the outputs of the model run.

#### 2. Use `docker cp` to copy files out of the container

The other option for getting model results out of the container is to use the [`docker cp`](https://docs.docker.com/engine/reference/commandline/cp/). After a successful `docker run` execution you can retrieve data from the container by doing:

```bash
# where CONTAINER is the name of the container
$ docker cp CONTAINER:/app/RAMP-UA/devon_data/output /path/to/desired/destination
```

## Creating releases
This repository takes advantage of a GitHub action for [creating tagged releases](https://github.com/marvinpinto/action-automatic-releases) using [semantic versioning](https://semver.org/).

To initiate the GitHub action and create a release:

```bash
$ git checkout branch

$ git tag -a v0.1.2 -m 'tag comment about release'

$ git push --tags
```
Once pushed the action will initiate and attempt to create a release.

## Documentation

Documentation for this package is generated using [Sphinx](https://www.sphinx-doc.org/en/master/index.html). It uses the `sphinx.ext.autodoc` extension to populate the documentation from existing docstrings.

To build the documentation locally:

```bash

$ cd docs/

$ make html

```

If a new module is added you will need to create new `.rst` files using the `sphinx-apidoc` command.

```bash

$ cd docs/

$ sphinx-apidoc -f -o source/ ../new_module/

```

This will generate new `.rst` files from the new modules docstrings that can then be rendered into html by running `make html`.
