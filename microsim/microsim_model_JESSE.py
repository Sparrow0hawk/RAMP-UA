#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Core RAMP-UA model.

Created on Wed Apr 29 19:59:25 2020

@author: nick
"""


import sys
sys.path.append("microsim") # This is only needed when testing. I'm so confused about the imports
from activity_location import ActivityLocation
#from microsim.microsim_analysis import MicrosimAnalysis
from microsim_analysis import MicrosimAnalysis
from column_names import ColumnNames
from utilities import Optimise

import pandas as pd
pd.set_option('display.expand_frame_repr', False)  # Don't wrap lines when displaying DataFrames
#pd.set_option('display.width', 0)  # Automatically find the best width
import numpy as np
import glob
import os
import random
import time
import re  # For analysing file names
import warnings
from collections.abc import Iterable   # drop `.abc` with Python 2.7 or lower
from typing import List, Dict
from tqdm import tqdm  # For a progress bar
import click  # command-line interface
import pickle # to save data
import swifter


class Microsim:
    """
    A class used to represent the microsimulation model.

    TODO: Document class. Please use reStructuredText format:
    https://www.sphinx-doc.org/en/master/usage/restructuredtext/basics.html
    ...

    Attributes
    ----------
    XXXX

    Methods
    -------
    XXXX
    """



    def __init__(self,
                 study_msoas: List[str] = [],
                 danger_multiplier = 1.0, risk_multiplier = 1.0,
                 random_seed: float = None, read_data: bool = True,
                 data_dir = "data", testing=False
                 ):
        """
        Microsim constructor. This reads all of the necessary data to run the microsimulation.
        ----------
        :param study_msoas: An optional list of MSOA codes to restrict the model to
        :param danger_multiplier: Danger assigned to a place if an infected individual visits it
        is calcuated as duration * flow * danger_multiplier.
        :param risk_multiplier: Risk that individuals get from a location is calculatd as
        duration * flow * * danger * risk_multiplier
        :param random_seed: A optional random seed to use when creating the class instance. If None then
            the current time is used.
        :param read_data: Optionally don't read in the data when instantiating this Microsim (useful
            in debugging).
        :param data_dir: Optionally provide a root data directory
        :param testing: Optionally turn off some exceptions and replace them with warnings (only good when testing!)
        """

        # Administrative variables that need to be defined
        self.iteration = 0
        self.danger_multiplier = danger_multiplier
        self.risk_multiplier = risk_multiplier
        self.random = random.Random(time.time() if random_seed is None else random_seed)
        Microsim.DATA_DIR = data_dir
        Microsim.testing = testing
        if self.testing:
            warnings.warn("Running in testing mode. Some exceptions will be disabled.")

        if not read_data:  # Optionally can not do this, usually for debugging
            return

        # Now the main chunk of initialisation is to read the input data.

        # This is the main population of individuals and their households
        self.individuals, self.households = Microsim.read_msm_data()

        # The individuals are at MSOA level, so use that file to construct a list of areas
        self.all_msoas = Microsim.extract_msoas_from_indiviuals(self.individuals)

        # Now we have the 'core population'. Keep copies of this but continue to use the 'individuals' data frame
        # Not saving as they're not needed and will take up some memory
        self.core_individuals = self.individuals.copy()
        self.core_households = self.households.copy()

        # See if we need to restrict by a study area (optional parameter passed by the user).
        # If so, then remove individuals and households not in the study area
        self.study_msoas, self.individuals, self.households = \
            Microsim.check_study_area(self.all_msoas, study_msoas, self.individuals, self.households)

        # For each type of activity (store, retail, etc), create ActivityLocation objects to keep all the
        # required information together.
        self.activity_locations: Dict[str, ActivityLocation] = {}

        #
        # ********** How to assign activities for the population **********
        #
        # For each 'activity' (e.g shopping), we need to store the following things:
        #
        # 1. A data frame of the places where the activities take place (e.g. a list of shops). Refered to as
        # the 'locations' dataframe. Importantly this will have a 'Danger' column which records whether infected
        # people have visited the location.
        #
        # 2. Columns in the individuals data frame that says which locations each individual is likely to do that
        # activity (a list of 'venues'), how likely they are to do to the activity (a list of 'flows'), and the
        # duration spent doing the activity.
        #
        # For most activities, there are a number of different possible locations that the individual
        # could visit. The 'venues' column stores a list of indexes to the locations dataframe. E.g. one individual
        # might have venues=[2,54,19]. Those numbers refer to the *row numbers* of locations in the locations
        # dataframe. So venue '2' is the third venue in the list of all the locaitons associated with that activity.
        # Flows are also a list, e.g. for that individual the flows might be flows=[0.8,0.1,0.1] which means they
        # are most likely to go to venue with index 2, and less likely to go to the other two.
        #
        # For some activities, e.g. 'being at home', each individual has only a single location and one flow, so their
        # venues and flows columns will only be single-element lists.
        #
        # For multi-venue activities, the process is as follows (easiest to see the retail or shopping example):
        # 1. Create a dataframe of individual locations and use a spatial interation model to estimate flows to those
        # locations, creating a flow matrix. E.g. the `read_retail_flows_data()` function
        # 2. Run through the flow matrix, assigning all individuals in each MSOA the appropriate flows. E.g. the
        # `add_individual_flows()` function.
        # 3. Create an `ActivityLocation` object to store information about these locations in a standard way.
        # When they are created, these `ActivityLocation` objects will also add another column
        # to the individuals dataframe that records the amount of time they spend doing the activity
        # (e.g. 'RETAIL_DURATION'). These raw numbers were attached earlier in `attach_time_use_and_health_data`.
        # (Again see the retail example below).
        # These ActivityLocation objects are stored in a dictionary (see `activity_locations` created above). This makes
        # it possible to run through all activities and calculate risks and dangers using the same code.
        #

        # Begin by attach a load of transport attributes to each individual. This includes essential information on
        # the durations that people spend doing activities.
        # (actually this is more like attaching the previous microsim to these new data, see the function for details)
        # This also creates flows and venues columns for the journeys of individuals to households, and makes a new
        # households dataset to replace the one we read in above.
        home_name = "Home"  # How to describe flows to people's houses
        self.individuals, self.households = Microsim.attach_time_use_and_health_data(self.individuals, home_name, self.study_msoas)
        # Create 'activity locations' for the activity of being at home. (This is done for other activities,
        # like retail etc, when those data are read in later.
        self.activity_locations[home_name] = ActivityLocation(name=home_name, locations=self.households,
                                                              flows=None, individuals=self.individuals,
                                                              duration_col="phome")

        # Generate travel time columns and assign travel modes to some kind of risky activity (not doing this yet)
        #self.individuals = Microsim.generate_travel_time_colums(self.individuals)
        # One thing we do need to do (this would be done in the function) is replace NaNs in the time use data with 0
        for col in ["pwork", "pschool", "pshop", "pleisure", "ptransport", "pother"]:
            self.individuals[col].fillna(0, inplace=True)

        # Read Retail flows data
        retail_name = "Retail" # How to refer to this in data frame columns etc.
        stores, stores_flows = Microsim.read_retail_flows_data(self.study_msoas)  # (list of shops and a flow matrix)
        Microsim.check_sim_flows(stores, stores_flows)
        # Assign Retail flows data to the individuals
        self.individuals = Microsim.add_individual_flows(retail_name, self.individuals, stores_flows)
        self.activity_locations[retail_name] = \
            ActivityLocation(retail_name, stores, stores_flows, self.individuals, "pshop")

        # Read Schools (primary and secondary)
        primary_name = "PrimarySchool"
        secondary_name = "SecondarySchool"
        schools, primary_flows, secondary_flows = \
            Microsim.read_school_flows_data(self.study_msoas)  # (list of schools and a flow matrix)
        Microsim.check_sim_flows(schools, primary_flows)
        Microsim.check_sim_flows(schools, secondary_flows)
        # Assign Schools
        # TODO: need to separate primary and secondary school duration. At the moment everyone is given the same
        # duration, 'pschool', which means that children will be assigned a PrimarySchool duration *and* a
        # seconary school duration, regardless of their age. I think the only way round this is to
        # make two new columns - 'pschool_primary' and 'pschool_seconary', and set these to either 'pschool'
        # or 0 depending on the age of the child.
        # Also all schools are in the same dataframe, we need to make copies of the dataframe. Otherwise at the start
        # of each iteration when the secondary school dangers are set to 0 (danger is not cumulative) it will override
        # the primary school dangers that were calculated first.
        self.individuals = Microsim.add_individual_flows(primary_name, self.individuals, primary_flows)
        self.activity_locations[primary_name] = \
            ActivityLocation(primary_name, schools.copy(), primary_flows, self.individuals, "pschool")
        self.individuals = Microsim.add_individual_flows(secondary_name, self.individuals, secondary_flows)
        self.activity_locations[secondary_name] = \
            ActivityLocation(secondary_name, schools.copy(), secondary_flows, self.individuals, "pschool")
        del schools  # No longer needed as we gave copies to the ActivityLocation

        # Assign work. Each individual will go to a virtual office depending on their occupation (all accountants go
        # to the virtual accountant office etc). This means we don't have to calculate a flows matrix (similar to homes)
        # Occupation is taken from column soc2010b in individuals df
        possible_jobs = sorted(self.individuals.soc2010b.unique())  # list of possible jobs in alphabetical order
        workplaces = pd.DataFrame({'ID': range(0, 0+len(possible_jobs))})  # df with all possible 'virtual offices'
        Microsim._add_location_columns(workplaces, location_names=possible_jobs)
        work_name = "Work"
        self.individuals = Microsim.add_work_flows(work_name, self.individuals, workplaces)
        self.activity_locations[work_name] = ActivityLocation(name=work_name, locations=workplaces, flows=None,
                                                              individuals=self.individuals, duration_col="pwork")

        # Some flows will be very complicated numbers. Reduce the numbers of decimal places across the board.
        # This makes it easier to write out the files
        for name in tqdm(self.activity_locations.keys(), desc="Rounding all flows"):
            self.individuals[f"{name}{ColumnNames.ACTIVITY_FLOWS}"] = \
                self.individuals[f"{name}{ColumnNames.ACTIVITY_FLOWS}"].swifter.progress_bar(False).apply(
                    lambda flows: [round(flow, 5) for flow in flows])


        # Add some necessary columns for the disease and assign initial SEIR status
        self.individuals = Microsim.add_disease_columns(self.individuals)
        self.individuals = Microsim.assign_initial_disease_status(self.individuals)

        return

    @classmethod
    def read_msm_data(cls) -> (pd.DataFrame, pd.DataFrame):
        """Read the csv files that have the indivduls and households

        :return a tuple with two pandas dataframes representing individuls (0) and households (1)
        """

        msm_dir = os.path.join(cls.DATA_DIR, "msm_data")

        # Can't just read in all the files because the microsimulation restarts the house and index numbering with
        # each file, but we need the IDs to be consistent across the whole area. So read the files in one-by-one
        # and make sure houses and individual IDs are unique
        household_files = glob.glob(os.path.join(msm_dir, 'ass_hh_*_OA11_2020.csv') )
        if len(household_files) == 0:
            raise Exception(f"No household csv files found in {msm_dir}.",
                            f"Have you downloaded and extracted the necessary data? (see {cls.DATA_DIR} README).",
                            f"The directory has these files in it: {os.listdir(msm_dir)}")
        individual_files = glob.glob(os.path.join(msm_dir, 'ass_*_MSOA11_2020.csv'))
        if len(individual_files) == 0:
            raise Exception(f"No individual csv files found in {msm_dir}.",
                            f"Have you downloaded and extracted the necessary data? (see {cls.DATA_DIR} README)")
        assert (len(household_files) == len(individual_files))
        household_files.sort()
        individual_files.sort()

        # Create a DataFrame from each file, then concatenate them later
        house_dfs = []
        indiv_dfs = []

        # Keep track of the house and person indices
        # (No longer doing this; will create a new unique identifier from Area, HID, PID combination later.
        #PID_counter = 0
        #HID_counter = 0
        warns = [] # Save warnings until later otherwise it messes with the progress bar (not important)
        for i in tqdm(range(len(household_files)), desc="Reading raw microsim data"):
            house_file = household_files[i]
            indiv_file = individual_files[i]
            area = re.search(r".*?ass_hh_(E\d.*?)_OA.*", house_file).group(1)  # Area is in the file name
            # (and check that both files refer to the same area)
            assert area == re.search(r".*?ass_(E\d.*?)_MSOA.*", indiv_file).group(1)

            house_df = pd.read_csv(house_file)
            indiv_df = pd.read_csv(indiv_file)

            # Add some useful columns

            # The local authority
            house_df["Local_Authority"] = area
            indiv_df["Local_Authority"] = area

            # Look up the OA of the house that each individual lives in (individuals are MSOA) and vice versa
            indiv_df["House_OA"] = indiv_df.set_index("PID").merge(house_df.set_index("HRPID").rename(columns={"Area": "House_OA"}), how="left")["House_OA"]
            house_df["HRP_MSOA"] = house_df.set_index("HRPID").merge(indiv_df.set_index("PID").rename(columns={"Area": "HRP_MSOA"}), how="left")["HRP_MSOA"]
            # Check that the number of OAs in the household file is the same as those in the individuals file
            # (there are some NA's where individuals weren't matched to households, this is OK and dealt with later)
            if len(house_df.Area.unique()) > len(indiv_df.House_OA.dropna().unique()):
                warns.append(f"When reading LA {area} there were {len(house_df.Area.unique()) - len(indiv_df.House_OA.dropna().unique())} "
                              f"output areas that had households with no individuals living in them")
            elif len(house_df.Area.unique()) < len(indiv_df.House_OA.dropna().unique()):
                raise Exception("Individuals have been assigned to more House OAs than actually exist!")

            # Increment the counters
            #house_df["HID"] = house_df["HID"].apply(lambda x: x + HID_counter)
            #house_df["HRPID"] = house_df["HRPID"].apply(lambda x: x + PID_counter)  # Also increase the HRP

            #indiv_df["PID"] = indiv_df["PID"].apply(lambda x: x + PID_counter)
            #indiv_df["HID"] = indiv_df["HID"].apply(lambda x: x + HID_counter)  # Also increase the link to HID

            #HID_counter = max(house_df["HID"]) + 1  # Want next counter to start at one larger than current
            #PID_counter = max(indiv_df["PID"]) + 1

            # Save the dataframes for concatination later
            house_dfs.append(house_df)
            indiv_dfs.append(indiv_df)

        # Any warnings?
        for w in warns:
            warnings.warn(w)

        # Concatenate the files
        households = pd.concat(house_dfs)
        individuals = pd.concat(indiv_dfs)

        # Manually set some column types
        # Should save a bit of memory because not duplicating area strings
        # (No longer doing this beacuse it makes comparisons annoying when we have 2 'area' columns
        # after reading time use & health data. Can work round that if we really need the memory).
        # individuals["Area"] = individuals["Area"].astype('category')

        # Make sure HIDs and PIDs are unique
        #assert len(households["HID"].unique()) == len(households)
        #assert len(individuals["PID"].unique()) == len(individuals)

        # Set the index
        #households.set_index("HID", inplace=True, drop=False)
        #individuals.set_index("PID", inplace=True, drop=False)

        # Make sure the combination of [Area, HID, PID] for individuals, and [Area, HID] for areas, are unique
        assert len(individuals.loc[:, ["Area", "HID", "PID"]].drop_duplicates()) == len(individuals)
        assert len(households.loc[:, ["Area", "HID"]].drop_duplicates()) == len(households)

        # Some people aren't matched to households for some reason. Their HID == -1. Remove them
        no_hh = individuals.loc[individuals.HID==-1]
        if len(no_hh) > 0:
            warnings.warn(f"There are {len(no_hh)} individuals who were not matched to a house in the original "
                          f"data. They will be removed.")
        individuals = individuals.loc[individuals.HID != -1]
        # Now everyone should have a household. This will raise an exception if not. (unless testing)
        # TODO uncomment below to check that no people without households have been introduced
        # (commented while developing beause it is very slow
        warnings.warn("Not checking that no homeless were introduced, uncomment when running properly")
        #Microsim._check_no_homeless(individuals, households, warn=True if Microsim.testing else False )

        print("Have read files:",
              f"\n\tHouseholds:  {len(house_dfs)} files with {len(households)}",
              f"households in {len(households.Area.unique())} areas",
              f"\n\tIndividuals: {len(indiv_dfs)} files with {len(individuals)}",
              f"individuals in {len(individuals.Area.unique())} areas")

        return (individuals, households)

    @classmethod
    def _check_no_homeless(cls, individuals, households, warn=True):
        """
        Check that each individual has a household. NOTE: this only works for the raw mirosimulation data.
        Once the health data has been attached this wont work becuase the unique identifiers change.
        If this function is still needed then it will need to take the specific IDs as arguments, but this is
        a little complicated because some combination of [Area, HID, (PID)] is needed for unique identification.

        :param individuals:
        :param households:
        :param warn: Whether to warn (default, True) or raise an exception (False)
        :return: True if there are no homeless, False otherwise (unless `warn==False` in which case an
        exception is raised).
        :raise: An exception if `warn==False` and there are individuals without a household
        """
        print("Checking no homeless (all individuals assigned to a household) ...",)
        # This will fail if used on anything other than the raw msm data because once I read in the
        # health data the PID and HID columns are renamed to prevent them being accidentally used.
        assert "PID" in individuals.columns and "HID" in households.columns
        # Households in the msm are uniquely identified by [Area,HID] combination.
        # Individuals are identified by [House_OA,HID,PID]
        hids = households.set_index(["Area", "HID"]) # Make a new dataset with a unique index for households
        # Find individuals who do not have a related entry in the households dataset
        homeless = [(area, hid, pid) for area, hid, pid in individuals.loc[:, ["House_OA", "HID", "PID"]].values if (area, hid) not in hids.index]
        # (version using apply isn't quicker)
        #h2 = individuals.reset_index().loc[:, ["House_OA", "HID", "PID"]].swifter.apply(
        #    lambda x: x[2] if (x[0], x[1]) in hids.index else None, axis=1)
        # (Vectorised version doesn't quite work sadly)
        #h2 = np.where(individuals.loc[:, ["House_OA", "HID", "PID"]].isin(hids.index), True, False)
        if len(homeless) > 0:
            msg = f"There are {len(homeless)} individuals without an associated household (HID)."
            if warn:
                warnings.warn(msg)
                return False
            else:
                raise Exception(msg)
        print("... finished checking homeless")
        return True

    @classmethod
    def extract_msoas_from_indiviuals(cls, individuals: pd.DataFrame) -> List[str]:
        """
        Analyse a DataFrame of individuals and extract the unique MSOA codes, returning them as a list in ascending
        order
        :param individuals:
        :return:
        """
        areas = list(individuals.Area.unique())
        areas.sort()
        return areas

    @classmethod
    def check_study_area(cls, all_msoas: List[str], study_msoas: List[str], individuals: pd.DataFrame,
                         households: pd.DataFrame) \
            -> (List[str], pd.DataFrame, pd.DataFrame):
        """
        It is possible to optionally subset all MSOAs used in the analysis (i.e. create a study area). If so, then
        remove all individuals and households who are outside of the study area, returning new DataFrames.
        :param all_msoas: All areas that could be used (e.g. all MSOAs in the UK)
        :param study_msoas: A subset of those areas that could be used
        :param individuals: The DataFrame of individuals.
        :param households:  The DataFrame of households
        :return: A tuple containing:
          - [0] a list of the MSOAs being used (either all, or just those in the study area)
          - [1] the new list of individuals (which might be shorter than the original if using a smaller study area)
          - [2] the new list of households
        """
        # No study area subset provided, use the whole area.
        if study_msoas is None or len(study_msoas) == 0:
            return all_msoas, individuals, households
        # Check that all areas in both arrays are unique
        for d, l in [("all msoas", all_msoas), ("study area msoas", study_msoas)]:
            if len(l) != len(set(l)):
                raise Exception(f"There are some duplicate areas in the {d} list: {l}.")
        for area in study_msoas:
            if area not in all_msoas:
                raise Exception(f"Area '{area}' in the list of case study areas is not in the national dataset")

        # Which individuals and houeholds to keep
        individuals_to_keep = individuals.loc[individuals.Area.isin(study_msoas), :]
        assert (len(individuals_to_keep.Area.unique()) == len(study_msoas))
        households_to_keep = households.loc[households.HID.isin(individuals_to_keep.HID), :]
        print(f"\tUsing a subset study area consisting of {len(study_msoas)} MSOAs.\n"
              f"\tBefore subsetting: {len(individuals)} individuals, {len(households)} househods.",
              f"\tAfter subsetting: {len(individuals_to_keep)} individuals, {len(households_to_keep)} househods.")

        # Check no individuals without households have been introduced (raise an exception if so)
        # TODO uncomment below to check that no people without households have been introduced
        # (commented while developing beause it is very slow
        warnings.warn("Not checking that no homeless were introduced, uncomment when running properly")
        #Microsim._check_no_homeless(individuals, households, warn=False)

        return (study_msoas, individuals_to_keep, households_to_keep)



    @classmethod
    def attach_time_use_and_health_data(cls, individuals: pd.DataFrame, home_name: str, study_msoas: List[str]=None) -> pd.DataFrame:
        """Attach time use data (proportions of time people spend doing the different activities) and additional
        health data.

        Actually what happens is the time-use & health data are taken as the main population, and individuals
        in the original data are linked in to this one. This is because the linking process (done elsewhere)
        means that households and invididuals are duplicated.

        Note that we can't link this file back to the original households, so just create a new households
        dataframe.

        :param individuals: The dataframe of individuals that the new columns will be added to
        :param home_name: A string to describe flows to people's homes (probably 'Home')
        :param study_msoas: Optional study area to restrict by (all individuals not in these MSOAs will be removed)
        :return A tuple with new dataframes of individuals and households
        """
        print("Attaching time use and health data for Devon... ", )
        #filename = os.path.join(cls.DATA_DIR, "devon-tu_health", "Devon_simulated_TU_health.txt")
        #filename = os.path.join(cls.DATA_DIR, "devon-tu_health", "Devon_keyworker.txt")
        filename = os.path.join(cls.DATA_DIR, "devon-tu_health", "Devon_Complete.txt")

        tuh = pd.read_csv(filename)
        tuh = Optimise.optimize(tuh)  # Reduce memory of tuh where possible.

        # Drop people that weren't matched to a household originally
        nohh = len(tuh.loc[tuh.hid == -1])
        if nohh > 0:
            warnings.warn(f"{nohh} / {len(tuh)} individuals in the TUH data had not originally been matched "
                          f"to a household. They're being removed")
        tuh = tuh.loc[tuh.hid != -1]

        # Indicate that HIDs and PIDs shouldn't be used as indices as they don't uniquely
        # identify indivuals / households in this health data and be specif about what 'Area' means
        tuh = tuh.rename(columns={'hid': '_hid', 'pid': '_pid'})
        individuals = individuals.rename(columns={"PID": "_PID", "HID": "_HID"})
        # Not sure why HID and PID aren't ints
        individuals["_HID"] = individuals["_HID"].apply(int)
        individuals["_PID"] = individuals["_PID"].apply(int)

        # Remove any individuals not in the study area
        original_count = len(tuh)
        tuh = tuh.loc[tuh.area.isin(study_msoas), :]
        print(f"\tWhen setting the study area, {original_count-len(tuh)} individuals removed from the time use & health data")
        # Now should have nearly the same number of people (maybe not exactly due to how individuals are
        # allocated to areas in the component set matching
        diff = (len(tuh) - len(individuals)) / len(tuh)
        if diff > 0.02:  # More than 2% (arbitrary)
            raise Exception(f"The number of individuals in the raw msm ({len(individuals)}) and the time use & health "
                            f"data ({len(tuh)}) are very different ({diff*100}%). This may be an error.")

        # Make a new, unique id for each individual (PIDs have been replicated so no longer uniquely idenfity individuals}
        assert len(tuh.index) == len(tuh)  # Index should have been set to row number when tuh was read in
        tuh.insert(0, "ID", tuh.index, allow_duplicates=False)  # Insert into first position

        # Link to original individual data from the raw microsim. [MSOA, HID, PID] can link them.
        # We want these columns from the individual dataset (but for convenience also get an extra _PID column
        # So that they don't have to be hard coded)
        #["DC1117EW_C_SEX", "DC1117EW_C_AGE", "DC2101EW_C_ETHPUK11", "_HID", "Local_Authority", "House_OA"]
        tuh = tuh.merge(individuals, how="left", left_on = ["area", "_hid", "_pid"],
                           right_on = ["Area", "_HID", "_PID"], validate="many_to_one")

        # To check that the join worked we can't have categories, otherwise we can't compare areas easily
        # (surely it's possibel to compare columns with different categories but I can't work it out).
        # (The other 'Area' column, that came from the individuals dataframe is already a string
        tuh['area'] = tuh.area.astype(str)

        # Check the join has worked: (if it fails, can use the following to find the rows that are different)
        assert len(tuh) == \
          len(tuh.loc[(tuh["area"] == tuh["Area"]) & (tuh["_hid"] == tuh["_HID"]) & (tuh["_pid"] == tuh["_PID"]), :])

        # Should have no nas in the columns that were just merged in
        assert len(tuh.loc[tuh.House_OA.isna(), :]) == 0
        assert len(tuh.loc[tuh.DC2101EW_C_ETHPUK11.isna(), :]) == 0

        tuh = Optimise.optimize(tuh)  # Now that new columns have been added

        #
        # ********** Create households dataframe *************
        #

        # This replaces the original households dataframe that we read from the msm data as we can no longer link back
        # to that one.

        # Go through each individual. House members can be identified because they have the same [Area, HID]
        # combination.
        # Maintain a dictionary of (Area, HID) -> House_ID that records a new ID for each house
        # Each time a new [Area, HID] combination is found, create a new entry in the households dictionary for that
        # household, generate a House_ID, and record that in the dictionary.
        # When existing (Area, HID) combinations are found, look up the ID in the dataframe and record it for that
        # individual
        # Also, maintain a list of house_ids in the same order as individuals in the tuh data which can be used later
        # when we link from the individuls in the TUH data to their house id

        # This is the main dictionary. It maps (Area, HID) to house id numbers, along with some more information:
        house_ids_dict = {}  # (Area, HID) -> [HouseIDNumber, NumPeople, area, hid]

        house_ids_list = []  # ID of each house for each individual
        house_id_counter = 0  # Counter to generate new HouseIDNumbers
        unique_individuals = []  # Also store all [Area, HID, PID] combinations to check they're are unique later

        # Maybe quicker to loop over 3 lists simultaneously than through a DataFrame
        _areas = list(tuh["area"])
        _hids = list(tuh["_hid"])
        _pids = list(tuh["_pid"])

        for i, (area, hid, pid) in enumerate(zip(_areas, _hids, _pids)):
            #print(i, area, hid, pid)
            unique_individuals.append((area, hid, pid))
            house_key = (area, hid)  # Uniqely identifies a household
            house_id_number = -1
            try:  # If this lookup works then we've seen this house before. Get it's ID number and increase num people in it
                house_info = house_ids_dict[house_key]
                # Check the area and hid are the same as the one previously stored in the dictionary
                assert area == house_info[2] and hid == house_info[3]
                # Also check that the house key (Area, HID) matches the area and HID
                assert house_key[0] == house_info[2] and house_key[1] == house_info[3]
                # We need the ID number to tell the individual which their house is
                house_id_number = house_info[0]
                # Increse the number of people in the house and create a new list of info for this house
                people_per_house = house_info[1] + 1
                house_ids_dict[house_key] = [house_id_number, people_per_house, area, hid ]
            except KeyError:  # If the lookup failed then this is the first time we've seen this house. Make a new ID.
                house_id_number = house_id_counter
                house_ids_dict[house_key] = [ house_id_number, 1, area, hid]  # (1 is beacuse 1 person so far in the hosue)
                house_id_counter += 1
            assert house_id_number > -1
            house_ids_list.append(house_id_number)  # Remember the house for this individual

        assert len(unique_individuals) == len(tuh)
        assert len(house_ids_list) == len(tuh)
        assert len(house_ids_dict) == house_id_counter

        # While we're here, may as well also check that [Area, HID, PID] is a unique identifier of individuals
        # TODO FIND OUT FROM KARYN WHY THESE LEGTHS ARE DIFFERENT
        #assert len(tuh) == len(set(unique_individuals))

        # Done! Now can create the households dataframe
        households_df = pd.DataFrame(house_ids_dict.values(), columns=['House_ID', 'Num_People', 'area', '_hid'])
        households_df = Optimise.optimize(households_df)

        # And tell the individuals which house they live in
        tuh["House_ID"] = house_ids_list  # Assign each individuals to their household

        # Check all house IDs are unique and have same number as in TUH data
        assert len(frozenset(households_df.House_ID.unique())) == len(households_df)
        assert len(tuh.area.unique()) == len(tuh.area.unique())
        # Check that the area that the invidiual lives in is the same as the area their house is in
        temp_merge = tuh.merge(households_df, how="left", on = ["House_ID"], validate="many_to_one")
        assert len(temp_merge) == len(tuh)
        assert False not in list(temp_merge['area_x']==temp_merge['area_y'])

        # Add some required columns
        Microsim._add_location_columns(households_df, location_names=list(households_df.House_ID),
                                       location_ids=households_df.House_ID )
        # The new ID column should be the same as the House_ID
        assert False not in list(households_df.House_ID == households_df[ColumnNames.LOCATION_ID])

        # Add flows for each individual (this is easy, it's just converting their House_ID into a one-value list)
        # Names for the new columns
        venues_col = f"{home_name}{ColumnNames.ACTIVITY_VENUES}"
        flows_col = f"{home_name}{ColumnNames.ACTIVITY_FLOWS}"

        tuh[venues_col] = tuh["House_ID"].apply(lambda x: [x])
        tuh[flows_col] = [ [1.0] for _ in range(len(tuh))]

        print("... finished reading TU&H data.")

        return tuh, households_df


    @classmethod
    def generate_travel_time_colums(cls, individuals: pd.DataFrame) -> pd.DataFrame:
        """
        TODO Read the raw travel time columns and create standard ones to show how long individuals
        spend travelling on different modes. Ultimately these will be turned into activities
        :param individuals:
        :return:
        """

        # Some sanity checks for the time use data
        # Variables pnothome, phome add up to 100% of the day and
        # pwork +pschool +pshop+ pleisure +pescort+ ptransport +pother = phome

        # TODO go through some of these with Karyn, they don't all pass
        # Time at home and not home should sum to 1.0
        if False in list((individuals.phome + individuals.pnothome) == 1.0):
            raise Exception("Time at home (phome) + time not at home (pnothome) does not always equal 1.0")
        # These columns should equal time not at home
        # if False in list(tuh.loc[:, ["pwork", "pschool", "pshop", "pleisure",  "ptransport", "pother"]]. \
        #                         sum(axis=1, skipna=True) == tuh.pnothome):
        #    raise Exception("Times doing activities don't add up correctly")

        # Temporarily (?) remove NAs from activity columns (I couldn't work out how to do this in 1 line like:
        for col in ["pwork", "pschool", "pshop", "pleisure", "ptransport", "pother"]:
            individuals[col].fillna(0, inplace=True)

        # TODO assign activities properly. Need to map from columns in the dataframe to standard names
        # Assign time use for Travel (just do this arbitrarily for now, the correct columns aren't in the data).
        # travel_cols = [ x + ColumnNames.ACTIVITY_DURATION for x in
        #                 [ ColumnNames.TRAVEL_CAR, ColumnNames.TRAVEL_BUS, ColumnNames.TRAVEL_TRAIN, ColumnNames.TRAVEL_WALK ] ]
        # for col in travel_cols:
        #    tuh[col] = 0.0
        # OLD WAY OF HARD-CODING TIME USE CATEGORIES FOR EACH INDIVIDUAL
        # For now just hard code broad categories. Ultimately will have different values for different activities.
        # activities = ["Home", "Retail", "PrimarySchool", "SecondarySchool", "Work", "Leisure"]
        # col_names = []
        # for act in activities:
        #    col_name = act + ColumnNames.ACTIVITY_DURATION
        #    col_names.append(col_name)
        #    if act=="Home":
        #        # Assume XX hours per day at home (this is whatever not spent doing other activities)
        #        individuals[col_name] = 14/24
        #    elif act == "Retail":
        #        individuals[col_name] = 1.0/24
        #    elif act == "PrimarySchool":
        #        # Assume 8 hours per day for all under 12
        #        individuals[col_name] = 0.0 # Default 0
        #        individuals.loc[individuals[ColumnNames.INDIVIDUAL_AGE] < 12, col_name] = 8.0/24
        #    elif act == "SecondarySchool":
        #        # Assume 8 hours per day for 12 <= x < 19
        #        individuals[col_name] = 0.0  # Default 0
        #        individuals.loc[individuals[ColumnNames.INDIVIDUAL_AGE] < 19, col_name] = 8.0 / 24
        #        individuals.loc[individuals[ColumnNames.INDIVIDUAL_AGE] < 12, col_name] = 0.0
        #    elif act == "Work":
        #        # Opposite of school
        ##        individuals[col_name] = 0.0 # Default 0
        #        individuals.loc[individuals[ColumnNames.INDIVIDUAL_AGE] >= 19, col_name] = 8.0/24
        #    elif act == "Leisure":
        #        individuals[col_name] = 1.0/24
        #    else:
        #        raise Exception(f"Unrecognised activity: {act}")

        # Check that proportions add up to 1.0
        # For some reason this fails, but as far as I can see the proportions correctly sum to 1 !!
        # assert False not in (individuals.loc[:, col_names].sum(axis=1).round(decimals=4) == 1.0)

        ## Add travel data columns (no values yet)
        # travel_cols = [ x + ColumnNames.ACTIVITY_DURATION for x in ["Car", "Bus", "Walk", "Train"] ]
        # for col in travel_cols:
        #    individuals[col] = 0.0
        return individuals


    @classmethod
    def read_school_flows_data(cls, study_msoas: List[str]) -> (pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame):
        """
        Read the flows between each MSOA and the most likely schools attended by pupils in this area.
        All schools are initially read together, but flows are separated into primary and secondary

        :param study_msoas: A list of MSOAs in the study area (flows outside of this will be ignored)
        :return: A tuple of three dataframes. All schools, then the flows to primary and secondary
        (Schools, PrimaryFlows, SeconaryFlows). Although all the schools are one dataframe, no primary flows will flow
        to secondary schools and vice versa).
        """
        # TODO Need to read full school flows, not just those of Devon
        print("Reading school flow data for Devon...", )
        dir = os.path.join(cls.DATA_DIR, "devon-schools")

        # Read the schools (all of them)
        schools = pd.read_csv(os.path.join(dir, "exeter schools.csv"))
        # Add some standard columns that all locations need
        schools_ids = list(schools.index + 1)  # Mark counts from 1, not zero, so indices need to start from 1 not 0
        schools_names = schools.EstablishmentName  # Standard name for the location
        Microsim._add_location_columns(schools, location_names=schools_names, location_ids=schools_ids)

        # Read the flows
        primary_rows = []  # Build up all the rows in the matrix gradually then add all at once
        secondary_rows = []
        with open(os.path.join(dir, "DJS002.TXT")) as f:
            # Mark's file comes in batches of 3 lines, each giving different data. However, some lines overrun and are
            # read as several lines rather than 1 (hence use of dests_tmp and flows_tmp)
            count = 1
            oa = None
            oa_name = ""
            num_dests = None
            dests = None
            flows = None
            dests_tmp = None
            flows_tmp = None

            for lineno, raw_line in enumerate(f):
                # print(f"{lineno}: '{raw_line}'")
                line_list = raw_line.strip().split()
                if count == 1:  # primary/secondary school, OA and number of schools
                    sch_type = int(line_list[0])
                    assert sch_type == 1 or sch_type == 2  # Primary schools are 1, secondary 2
                    oa = int(line_list[1])
                    oa_name = study_msoas[oa-1] # The OA names are stored in a separate file temporarily
                    num_dests = int(line_list[2])
                elif count == 2:  # school ids
                    dests_tmp = [int(x) for x in line_list[0:]]  # Make the destinations numbers
                    # check if dests exists from previous iteration and add dests_tmp
                    if dests == None:
                        dests = dests_tmp
                    else:
                        dests.extend(dests_tmp)
                    if len(dests) < num_dests: # need to read next line
                        count = 1 # counteracts count being increased by 1 later
                    else:
                        assert len(dests) == num_dests
                elif count == 3:  # Flows per 1,000 pupils
                    flows_tmp = [float(x) for x in line_list[0:]]  # Make the destinations numbers
                    # check if dests exists from previous iteration and add dests_tmp
                    if flows == None:
                        flows = flows_tmp
                    else:
                        flows.extend(flows_tmp)
                    if len(flows) < num_dests: # need to read next line
                        count = 2 # counteracts count being increased by 1 later
                    else:
                        assert len(flows) == num_dests

                        # Have read all information for this area. Store the info in the flows matrix

                        # We should have one line in the matrix for each OA, and OA codes are incremental
                        # assert len(flow_matrix) == oa - 1
                        row = [0.0 for _ in range(len(schools))]  # Initially assume all flows are 0
                        for i in range(num_dests):  # Now add the flows
                            dest = dests[i]
                            flow = flows[i]
                            row[dest - 1] = flow  # (-1 because destinations are numbered from 1, not 0)
                        assert len([x for x in row if x > 0]) == num_dests  # There should only be N >0 flows
                        row = [oa, oa_name] + row  # Insert the OA number and code (don't know this yet now)

                        # Luckily Mark's file does all primary schools first, then all secondary schools, so we
                        # know that all schools in this area are one or the other
                        if sch_type == 1:
                            primary_rows.append(row)
                        else:
                            secondary_rows.append(row)
                        #rows.append(row)

                        # Add the row to the matrix. As the OA numbers are incremental they should equal the number
                        # of rows
                        # flow_matrix.loc[oa-1] = row
                        # assert len(flow_matrix) == oa
                        count = 0
                        # reset dests and flows
                        dests = None
                        flows = None

                count += 1

        # Have finished reading the file, now create the matrices. MSOAs as rows, school locations as columns
        columns = ["Area_ID", "Area_Code"]  # A number (ID) and full code for each MSOA
        columns += [f"Loc_{i}" for i in schools.index]  # Columns for each school

        primary_flow_matrix = pd.DataFrame(data=primary_rows, columns=columns)
        secondary_flow_matrix = pd.DataFrame(data=secondary_rows, columns=columns)
        #schools_flows = schools_flows.iloc[0:len(self.study_msoas), :]
        print(f"... finished reading school flows.")

        return schools, primary_flow_matrix, secondary_flow_matrix

    @classmethod
    def add_work_flows(cls, flow_type: str, individuals: pd.DataFrame, workplaces: pd.DataFrame) \
            -> (pd.DataFrame):
        """
        Create a dataframe of (virtual) work locations that individuals
        travel to. Unlike retail etc, each individual will have only one work location with 100% of flows there.
        :param flow_type: The name for these flows (probably something like 'Work')
        :param individuals: The dataframe of synthetic individuals
        :param workplaces:  The dataframe of workplaces (i.e. occupations)
        :return: The new 'individuals' dataframe (with new columns)
        """
        # Later on, add a check to see if occupations in individuals df are same as those in workplaces df??
        # Tell the individuals about which virtual workplace they go to
        venues_col = f"{flow_type}{ColumnNames.ACTIVITY_VENUES}"
        flows_col = f"{flow_type}{ColumnNames.ACTIVITY_FLOWS}"

        # Lists showing where individuals go, and what proption (here only 1 flow as only 1 workplace)
        # Need to do the flows in venues in 2 stages: first just add the venue, then turn that venu into a single-element
        # list (pandas complains about 'TypeError: unhashable type: 'list'' if you try to make the single-item lists
        # directly in the apply
        venues = list(individuals["soc2010b"].apply(
            lambda job: workplaces.index[workplaces["Location_Name"] == job].values[0] ) )
        venues = [ [x] for x in venues]
        individuals[venues_col] = venues
        individuals[flows_col] = [[1.0] for _ in range(len(individuals))] # Flows are easy, [1.0] to the single work venue
        return individuals

    @classmethod
    def read_retail_flows_data(cls, study_msoas: List[str]) -> (pd.DataFrame, pd.DataFrame):
        """
        Read the flows between each MSOA and the most commonly visited shops

        :param study_msoas: A list of MSOAs in the study area (flows outside of this will be ignored)
        :return: A tuple of two dataframes. One containing all of the flows and another
        containing information about the stores themselves.
        """
        # TODO Need to read full retail flows, not just those of Devon (temporarily created by Mark).
        # Will also need to subset the flows into areas of interst, but at the moment assume that we area already
        # working with Devon subset of flows
        print("Reading retail flow data for Devon...", )
        dir = os.path.join(cls.DATA_DIR, "devon-retail")

        # Read the stores
        stores = pd.read_csv(os.path.join(dir, "devon smkt.csv"))
        # Add some standard columns that all locations need
        stores_ids = list(stores.index + 1)  # Mark counts from 1, not zero, so indices need to start from 1 not 0
        store_names = stores.store_name # Standard name for the location
        Microsim._add_location_columns(stores, location_names=store_names, location_ids=stores_ids)


        # Read the flows
        rows = []  # Build up all the rows in the matrix gradually then add all at once
        total_flows = 0  # For info & checking
        with open(os.path.join(dir, "DJR002.TXT")) as f:
            count = 1  # Mark's file comes in batches of 3 lines, each giving different data

            # See the README for info about these variables. This is only tempoarary so I can't be bothered
            # to explain properly
            oa = None
            oa_name = ""
            num_dests = None
            dests = None
            flows = None

            for lineno, raw_line in enumerate(f):
                # print(f"{lineno}: '{raw_line}'")
                line_list = raw_line.strip().split()
                if count == 1:  # OA and number of destinations
                    oa = int(line_list[0])
                    if oa > len(study_msoas):
                        msg = f"Attempting to read more output areas ({oa}) than are present in the study area {study_msoas}."
                        if cls.testing:
                            warnings.warn(msg)
                        else:
                            raise Exception(msg)
                    oa_name = study_msoas[oa - 1]  # The OA names are stored in a separate file temporarily
                    num_dests = int(line_list[1])
                elif count == 2:  # Top N (=10) destinations in the OA
                    # First number is the OA (don't need this), rest are the destinations
                    assert int(line_list[0]) == oa
                    dests = [int(x) for x in line_list[1:]]  # Make the destinations numbers
                    assert len(dests) == num_dests
                elif count == 3:  # Distance to each store (not currently used)
                    pass
                elif count == 4:  # Flows per 1,000 trips
                    # First number is the OA (don't need this), rest are the destinations
                    assert int(line_list[0]) == oa
                    flows = [float(x) for x in line_list[1:]]  # Make the destinations numbers
                    assert len(flows) == num_dests
                    total_flows += sum(flows)

                    # Have read all information for this area. Store the info in the flows matrix

                    # We should have one line in the matrix for each OA, and OA codes are incremental
                    # assert len(flow_matrix) == oa - 1
                    row = [0.0 for _ in range(len(stores))]  # Initially assume all flows are 0
                    for i in range(num_dests):  # Now add the flows
                        dest = dests[i]
                        flow = flows[i]
                        row[dest - 1] = flow  # (-1 because destinations are numbered from 1, not 0)
                    assert len([x for x in row if x > 0]) == num_dests  # There should only be positive flows (no 0s)
                    row = [oa, oa_name] + row  # Insert the OA number and code (don't know this yet now)

                    rows.append(row)

                    # Add the row to the matrix. As the OA numbers are incremental they should equal the number
                    # of rows
                    # flow_matrix.loc[oa-1] = row
                    # assert len(flow_matrix) == oa
                    count = 0

                count += 1

        # Have finished reading the file, now create the matrix. MSOAs as rows, retail locations as columns
        columns = ["Area_ID", "Area_Code"]  # A number (ID) and full code for each MSOA
        columns += [f"Loc_{i}" for i in stores.index]  # Columns for each store
        flow_matrix = pd.DataFrame(data=rows, columns=columns)

        # Check that we haven't lost any flows (need two sums, once to get the flows for each row, then
        # to add up all rows
        total_flows2 = flow_matrix.iloc[:, 2:].apply(lambda row: sum(row)).sum()
        assert total_flows == total_flows2

        print(f"... read {total_flows} flows from {len(flow_matrix)} areas.")

        return stores, flow_matrix

    @classmethod
    def check_sim_flows(cls, locations: pd.DataFrame, flows: pd.DataFrame):
        """
        Check that the flow matrix looks OK, raising an error if not
        :param locations: A DataFrame with information about each location (destination)
        :param flows: The flow matrix itself, showing flows from origin MSOAs to destinations
        :return:
        """
        # TODO All MSOA codes are unique
        # TODO Locations have 'Danger' and 'ID' columns
        # TODO Number of destination columns ('Loc_*') matches number of locaitons
        # TODO Number of origins (rows) in the flow matrix matches number of OAs in the locations
        return

    @classmethod
    def _add_location_columns(cls, locations: pd.DataFrame, location_names: List[str], location_ids: List[int] = None):
        """
        Add some standard columns to DataFrame (in place) that contains information about locations.
        :param locations: The dataframe of locations that the columns will be added to
        :param location_names: Names of the locations (e.g shop names)
        :param location_ids: Can optionally include a list of IDs. An 'ID' column is always created, but if no specific
        IDs are provided then the ID will be the same as the index (i.e. the row number). If ids are provided then
        the ID column will be set to the given IDs, but the index will still be the row number.
        :return: None; the columns are added to the input dataframe inplace.
        """
        # Make sure the index will always be the row number
        locations.reset_index(inplace=True, drop=True)
        if location_ids is None:
            # No specific index provided, just use the index
            locations[ColumnNames.LOCATION_ID] = locations.index
        else:
            # User has provided a specific list of indices to use
            if len(location_ids) != len(locations):
                raise Exception(f"When adding the standard columns to a locations dataframe, a list of specific",
                                f"IDs has ben passed, but this list (length {len(location_ids)}) is not the same"
                                f"length as the locations dataframe (length {len(locations)}. The list of ids passed"
                                f"is: {location_ids}.")
            locations[ColumnNames.LOCATION_ID] = location_ids
        if len(location_names) != len(locations):
            raise Exception(f"The list of location names is not the same as the number of locations in the dataframe",
                            f"({len(location_names)} != {len(locations)}.")
        locations[ColumnNames.LOCATION_NAME] = location_names # Standard name for the location
        locations[ColumnNames.LOCATION_DANGER] = 0  # All locations have a disease danger of 0 initially
        #locations.set_index(ColumnNames.LOCATION_ID, inplace=True, drop=False)
        return None  # Columns added in place so nothing to return


    @classmethod
    def add_individual_flows(cls, flow_type: str, individuals: pd.DataFrame, flow_matrix: pd.DataFrame) -> pd.DataFrame:
        """
        Take a flow matrix from MSOAs to (e.g. retail) locations and assign flows to individuals.

        It a assigns the id of the destination of the flow according to its column in the matrix. So the first column
        that has flows for a destination is given index 0, the second is index 1, etc. This is probably not the same as
        the ID of the venue that they point to (e.g. the first store probably has ID 1, but will be given the index 0)
        so it is important that when the activity_locations are created, they are created in the same order as the
        columns that appear in the matix. The first column in the matrix must also be the first row in the locations
        data.
        :param flow_type: What type of flows are these. This will be appended to the column names. E.g. "Retail".
        :param individuals: The DataFrame contining information about all individuals
        :param flow_matrix: The flow matrix, created by (e.g.) read_retail_flows_data()
        :return: The DataFrame of individuals with new locations and probabilities added
        """

        # Check that there aren't any individuals who wont be given any flows
        if len(individuals.loc[-individuals.Area.isin(flow_matrix.Area_Code)]) > 0:
            raise Exception(f"Some individuals will not be assigned any flows to: '{flow_type}' because their"
                            f"MSOA is not in the flow matrix: "
                            f"{individuals.loc[-individuals.Area.isin(flow_matrix.Area_Code)]}.")

        # Check that there aren't any duplicate flows
        if len(flow_matrix) != len(flow_matrix.Area_Code.unique()):
            raise Exception("There are duplicate area codes in the flow matrix: ", flow_matrix.Area_Code)

        # Names for the new columns
        venues_col = f"{flow_type}{ColumnNames.ACTIVITY_VENUES}"
        flows_col = f"{flow_type}{ColumnNames.ACTIVITY_FLOWS}"

        # Create empty lists to hold the vanues and flows for each individuals
        individuals[venues_col] = [[] for _ in range(len(individuals))]
        individuals[flows_col] = [[] for _ in range(len(individuals))]

        # Use a hierarchical index on the Area to speed up finding all individuals in an area
        # (not sure this makes much difference).
        individuals.set_index(["Area", "ID"], inplace=True, drop=False)

        for area in tqdm(flow_matrix.values, desc=f"Assigning individual flows for {flow_type}"):  # Easier to operate over a 2D matrix rather than a dataframe
            oa_num: int = area[0]
            oa_code: str = area[1]
            # Get rid of the area codes, so are now just left with flows to locations
            area = list(area[2:])
            # Destinations with positive flows and the flows themselves
            dests = []
            flows = []
            for i, flow in enumerate(area):
                if flow > 0.0:
                    dests.append(i)
                    flows.append(flow)

            # Normalise the flows
            flows = Microsim._normalise(flows)

            # Now assign individuals in those areas to those flows
            # This ridiculous 'apply' line is the only way I could get pandas to update the particular
            # rows required. Something like 'individuals.loc[ ...] = dests' (see below) didn't work becuase
            # instead of inserting the 'dests' list itself, pandas tried to unpack the list and insert
            # the individual values instead.
            # individuals.loc[individuals.Area == oa_code, f"{flow_type}_Venues"] = dests
            # individuals.loc[individuals.Area == oa_code, f"{flow_type}_Probabilities"] = flow
            #
            # A quicker way to do this is probably to create N subsets of individuals (one table for
            # each area) and then concatenate them at the end.

            individuals.loc[oa_code, venues_col] = \
                individuals.loc[oa_code, venues_col].apply(lambda _: dests).values
            individuals.loc[oa_code, flows_col] = \
                individuals.loc[oa_code, flows_col].apply(lambda _: flows).values
            # individuals.loc[individuals.Area=="E02004189", f"{flow_type}_Venues"] = \
            #    individuals.loc[individuals.Area=="E02004189", f"{flow_type}_Venues"].apply(lambda _: dests)
            # individuals.loc[individuals.Area=="E02004189", f"{flow_type}_Probabilities"] = \
            #    individuals.loc[individuals.Area=="E02004189", f"{flow_type}_Probabilities"].apply(lambda _: flows)

        # Reset the index so that it's not the PID
        individuals.reset_index(inplace=True, drop=True)

        # Check everyone has some flows (all list lengths are >0)
        assert False not in (individuals.loc[:, venues_col].apply(lambda cell: len(cell)) > 0).values
        assert False not in (individuals.loc[:, flows_col].apply(lambda cell: len(cell)) > 0).values

        return individuals

    @classmethod
    def _normalise(cls, l: List[float], decimals=3) -> List[float]:
        """
        Normalise a list so that it sums to almost 1.0. Rounding might cause it not to add exactly to 1

        :param decimals: Optionally round to the number of decimal places. Default 3. If 'None' the do no rounding.
        """
        if not isinstance(l, Iterable):
            raise Exception("Can only work with iterables")
        if len(l) == 1:  # Special case for 1-item iterables
            return [1.0]

        l = np.array(l)  # Easier to work with numpy vectorised operators
        total = l.sum()
        l = l / total
        if decimals is None:
            return list(l)
        return [round(x, decimals) for x in l]

    def export_to_feather(self, path="export"):
        """
        Export the dataframes that represent the current model state. See also `import_from_feather`.
        :param path: Optional directory to write the files to (default '.')
        :return:
        """
        # TODO finish this function properly, at the moment it writes to my desktop
        # Export individuals. Need to drop the flows columns because feather can't currently export those
        individuals = self.individuals.copy()
        for activity_name, activity in self.activity_locations.items():
            individuals = individuals.drop(f"{activity_name}{ColumnNames.ACTIVITY_VENUES}", 1)
            individuals = individuals.drop(f"{activity_name}{ColumnNames.ACTIVITY_FLOWS}", 1)

        #feather.write_feather(individuals, "/Users/nick/Desktop/individuals.feather")
        # Include a CSV file to check
        individuals.to_csv("/Users/nick/Desktop/individuals.csv")
        # Export locations


    def import_from_feather(self, path="export"):
        pass

    @classmethod
    def add_disease_columns(cls, individuals: pd.DataFrame) -> pd.DataFrame:
        """Adds columns required to estimate disease prevalence"""
        individuals[ColumnNames.DISEASE_STATUS] = 0
        individuals[ColumnNames.DAYS_WITH_STATUS] = 0  # Also keep the number of days that have elapsed with this status
        individuals[ColumnNames.CURRENT_RISK] = 0  # This is the risk that people get when visiting locations.
        individuals[ColumnNames.MSOA_CASES] = 0  # Useful to count cases per MSOA
        individuals[ColumnNames.HID_CASES] = 0  # Ditto for the household
        return individuals

    @classmethod
    def assign_initial_disease_status(cls, individuals: pd.DataFrame) -> pd.DataFrame:
        """
        Create a new column to represent the initial disease status of the individuals and assign them
        an initial status. Also create a column to record the number of days with that status
        :param individuals: The dataframe containin synthetic individuals
        :return: A new DataFrame for the individuals with the additional column
        """
        print("Assigning initial disease status ...",)
        #individuals["Disease_Status"] = [random.choice( range(0,4)) for _ in range(len(individuals))]
        # THIS WILL NEED TO BE DONE PROPERLY IN ANOTHER PROCESS (R?)
        print(f"... finished assigning initial status for {len(individuals)} individuals.")
        return individuals


    def update_venue_danger_and_risks(self, decimals=10):
        """
        Update the danger score for each location, based on where the individuals who have the infection visit.
        Then look through the individuals again, assigning some of that danger back to them as 'current risk'.

        :param risk_multiplier: Risk is calcuated as duration * flow * risk_multiplier.
        :param decimals: Number of decimals to round the indivdiual risks and dangers to (defult 10). If 'None'
                        then do no rounding
        """
        print("\tUpdating danger associated with visiting each venue")

        # Make a new list to keep the new risk for each individual (better than repeatedly accessing the dataframe)
        # Make this 0 initialy as the risk is not cumulative; it gets reset each day
        current_risk = [0] * len(self.individuals)

        #for name in tqdm(self.activity_locations, desc=f"Updating dangers and risks for activity locations"):
        for name in self.activity_locations:

            #
            # ***** 1 - update dangers of each venue (infected people visitting places)
            #

            print(f"\t\t{name} activity")
            # Get the details of the location activity
            activity_location = self.activity_locations[name]  # Pointer to the ActivityLocation object
            # Create a list to store the dangers associated with each location for this activity.
            # Assume 0 initially, it should be reset each day
            loc_dangers = [0] * len(activity_location.get_dangers())
            #loc_dangers = activity_location.get_dangers()  # List of the current dangers associated with each place


            # Now look up those venues in the table of individuals
            venues_col = f"{name}{ColumnNames.ACTIVITY_VENUES}" # The names of the venues and
            flows_col = f"{name}{ColumnNames.ACTIVITY_FLOWS}"   # flows in the individuals DataFrame
            durations_col = f"{name}{ColumnNames.ACTIVITY_DURATION}"   # flows in the individuals DataFrame

            # 2D lists, for each individual: the venues they visit, the flows to the venue (i.e. how much they visit it)
            # and the durations (how long they spend doing it)
            statuses = self.individuals.Disease_Status
            venues = self.individuals.loc[:, venues_col]
            flows = self.individuals.loc[:, flows_col]
            durations = self.individuals.loc[:, durations_col]
            assert len(venues) == len(flows) and len(venues) == len(statuses)
            for i, (v, f, s, duration) in enumerate(zip(venues, flows, statuses, durations)): # For each individual
                if s == 1 or s == 2 or s == 3: # Exposed (1), pre-symptomatic (2), symptomatic (3)
                    # v and f are lists of flows and venues for the individual. Go through each one
                    for venue_idx, flow in zip(v, f):
                        #print(i, venue_idx, flow, duration)
                        # Increase the danger by the flow multiplied by some disease risk
                        loc_dangers[venue_idx] += (flow * duration * self.risk_multiplier)


            #
            # ***** 2 - risks for individuals who visit dangerous venues
            #

            for i, (v, f, s, duration) in enumerate(zip(venues, flows, statuses, durations)):  # For each individual
                # v and f are lists of flows and venues for the individual. Go through each one
                for venue_idx, flow in zip(v, f):
                    #  Danger associated with the location (we just created these updated dangers in the previous loop)
                    danger = loc_dangers[venue_idx]
                    current_risk[i] += (flow * danger * duration * self.danger_multiplier)

            # Now we have the dangers associated with each location, apply these back to the main dataframe
            if decimals is not None: # Round the dangers?
                loc_dangers = [round(x, decimals) for x in loc_dangers]
            activity_location.update_dangers(loc_dangers)

        # Sanity check
        assert len(current_risk) == len(self.individuals)
        assert min(current_risk) >= 0  # Should not be risk less than 0

        # Round the current risks?
        if decimals is not None:
            current_risk = [round(x, decimals) for x in current_risk]

        self.individuals[ColumnNames.CURRENT_RISK] = current_risk

        return


    def update_disease_counts(self):
        """Update some disease counters -- counts of diseases in MSOAs & households -- which are useful
        in estimating the probability of contracting the disease"""
        # Update the diseases per MSOA and household
        # TODO replace Nan's with 0 (not a problem with MSOAs because they're a cateogry so the value_counts()
        # returns all, including those with 0 counts, but with HID those with 0 count don't get returned
        # Get rows with cases
        cases = self.individuals.loc[(self.individuals.Disease_Status == 1) |
                                     (self.individuals.Disease_Status == 2) |
                                     (self.individuals.Disease_Status == 3), :]
        # Count cases per area (convert to a dataframe)
        case_counts = cases["Area"].value_counts()
        case_counts = pd.DataFrame(data={"Area": case_counts.index, "Count": case_counts}).reset_index(drop=True)
        # Link this back to the orignal data
        self.individuals[ColumnNames.MSOA_CASES] = self.individuals.merge(case_counts, on="Area", how="left")["Count"]
        self.individuals[ColumnNames.MSOA_CASES].fillna(0, inplace=True)

        # Update HID cases
        case_counts = cases["House_ID"].value_counts()
        case_counts = pd.DataFrame(data={"House_ID": case_counts.index, "Count": case_counts}).reset_index(drop=True)
        self.individuals[ColumnNames.HID_CASES] = self.individuals.merge(case_counts, on="House_ID", how="left")["Count"]
        self.individuals[ColumnNames.HID_CASES].fillna(0, inplace=True)


    @classmethod
    def calculate_new_disease_status(cls, row: pd.Series, activity_locations: List[ActivityLocation]):
        """
        Given a row of the DataFrame of individuals (as pd.Series object) calculate their
        disease status
        :param row: The row (a Series) with the information about an individual
        :param activity_locations: The activity locations that people currently visit
        :return: The new disease status for that individual
        """
        # Can access th individual's data using the 'row' variable like a dictionary.
        #for activity_name, activity in activity_locations.items():
        #    # Work through each activity, and find the total risk
        #    assert activity_name == activity.get_name()
        #    venus = row[f"{activity_name}{ColumnNames.ACTIVITY_VENUES}"]
        #    flows = row[f"{activity_name}{ColumnNames.ACTIVITY_FLOWS}"]
        #    venus + flows  # Just to see how long this might take
        #    pass
        return row['Disease_Status'] # TEMP DON'T ACTUALLT DO ANYTHING

    def step(self) -> None:
        """
        Step (iterate) the model

        :param danger_multiplier: Danger assigned to a place if an infected individual visits it
        is calcuated as duration * flow * danger_multiplier.
        :param risk_multiplier: Risk that individuals get from a location is calculatd as
        duration * flow * risk_multiplier

        :return:
        """
        self.iteration += 1
        print(f"\nIteration: {self.iteration}\n")

        # Update the danger associated with each venue (i.e. the as people with the disease visit them they
        # become more dangerous) then update the risk to each individual of going to those venues.
        self.update_venue_danger_and_risks()


        # Update disease counters. E.g. count diseases in MSOAs & households
        self.update_disease_counts()

        # Calculate new disease status
        # ACTUALLY THIS WONT BE DONE HERE. THE DATA WILL BE PASSED TO R AND DEALT WITH THERE, GETTING A NEW
        # DISEASE STATUS COLUMN BACK
        print("Now should calculate new disease status")
        # (need to pass activity locations as well becasue the calculate_new_disease_status needs to be class-level
        # rather than object level (otherwise I couldn't get the argument passing to work properly)
        # tqdm.pandas(desc="Calculating new disease status") # means pd.apply() has a progress bar
        #self.individuals["Disease_Status"] = self.individuals.progress_apply(
        #   func=Microsim.calculate_new_disease_status, axis=1, activity_locations=self.activity_locations)


        # Increase the number of days that each individual has had their current status
        self.individuals["Days_With_Status"] = self.individuals["Days_With_Status"].apply(
            lambda x: x + 1)

        # Can export after every iteration if we want to
        #self.export_to_feather()

        # Do some analysis
        fig = MicrosimAnalysis.population_distribution(self.individuals, ["DC1117EW_C_AGE"])
        fig.show()
        #MicrosimAnalysis.location_danger_distribution(self.activity_locatons['Retail'], ["Danger"])



# PROGRAM ENTRY POINT
# Uses 'click' library so that it can be run from the command line
@click.command()
@click.option('--iterations', default=2, help='Number of model iterations. 0 means just run the initialisation')
@click.option('--data_dir', default="data", help='Root directory to load data from')
def run(iterations, data_dir):
    num_iter = iterations
    if num_iter==0:
        print("Iterations = 0. Not stepping model, just assigning the initial risks.")
    else:
        print("Running model for", num_iter, "iterations.")
    
    # To fix file path issues, use absolute/full path at all times
    # Pick either: get working directory (if user starts this script in place, or set working directory
    # Option A: copy current working directory:
    ## COMMENTED BY NICK os.chdir("..") # assume microsim subdir so need to go up one level
    base_dir = os.getcwd()  # get current directory
    # Option B: specific directory
    #base_dir = 'C:\\Users\\Toshiba\\git_repos\\RAMP-UA'
    # overwrite data_dir with full path
    data_dir = os.path.join(base_dir, data_dir)

    # Temporarily only want to use Devon MSOAs
    devon_msoas = pd.read_csv(os.path.join(data_dir, "devon_msoas.csv"), header=None, names=["x", "y", "Num", "Code", "Desc"])
    m = Microsim(study_msoas=list(devon_msoas.Code), data_dir=data_dir)

    # Temporily use dummy data for testing
    #data_dir = os.path.join(base_dir, "dummy_data")
    #m = Microsim(data_dir=data_dir, testing=True)
    
    # save initial m
    output_dir = os.path.join(data_dir, "output")
    pickle_out = open(os.path.join(output_dir, "m0.pickle"),"wb")
    pickle.dump(m, pickle_out)
    pickle_out.close()

    # collect disease status in new df (for analysis/visualisation)
    individuals_to_pickle = m.individuals
    individuals_to_pickle["DiseaseStatus0"] = m.individuals.Disease_Status
    
    # collect location dangers at time 0 in new df(for analysis/visualisation)
    # TODO make a function for this so that it doesn't need to be repeated in the for loop below
    for name in m.activity_locations:
        # Get the details of the location activity
        activity = m.activity_locations[name]  # Pointer to the ActivityLocation object
        loc_name = activity.get_name()  # retail, school etc
        loc_ids = activity.get_ids()  # List of the IDs of the locations 
        loc_dangers = activity.get_dangers()  # List of the current dangers

        locals()[loc_name+'_to_pickle'] = pd.DataFrame(list(zip(loc_ids, loc_dangers)), columns=['ID', 'Danger0'])


    # Step the model
    for i in range(num_iter):
        m.step()
        
        # add to items to pickle
        individuals_to_pickle["DiseaseStatus"+str(i+1)] = m.individuals.Disease_Status
        for name in m.activity_locations:
            # Get the details of the location activity
            activity = m.activity_locations[name]  # Pointer to the ActivityLocation object
            loc_name = activity.get_name()  # retail, school etc
            loc_ids = activity.get_ids()  # List of the IDs of the locations 
            loc_dangers = activity.get_dangers()  # List of the current dangers

            locals()[loc_name+'_to_pickle']["Danger"+str(i+1)] = loc_dangers

    
    # save individuals and danger dfs
    pickle_out = open(os.path.join(output_dir, "Individuals.pickle"),"wb")
    pickle.dump(individuals_to_pickle, pickle_out)
    pickle_out.close()  
    for name in m.activity_locations:
        # Get the details of the location activity
        activity = m.activity_locations[name]  # Pointer to the ActivityLocation object
        loc_name = activity.get_name()  # retail, school etc
               
        pickle_out = open(os.path.join(output_dir, loc_name+".pickle"),"wb")
        pickle.dump(locals()[loc_name+'_to_pickle'], pickle_out)
        pickle_out.close() 
       
        
    # Make some plots (save or show) - see seperate script for now
    # fig = MicrosimAnalysis.heatmap(individuals, ["Danger"])
    # fig.show()     
    # fig = MicrosimAnalysis.geogif(individuals, ["Danger"])
    # # save - can't display gif?   
        
        
    print("End of program")




def pop_init(data_dir):
    num_iter = 0

    # To fix file path issues, use absolute/full path at all times
    # Pick either: get working directory (if user starts this script in place, or set working directory
    # Option A: copy current working directory:
    ## COMMENTED BY NICK os.chdir("..") # assume microsim subdir so need to go up one level
    base_dir = os.getcwd()  # get current directory
    # Option B: specific directory
    #base_dir = 'C:\\Users\\Toshiba\\git_repos\\RAMP-UA'
    # overwrite data_dir with full path
    data_dir = os.path.join(base_dir, data_dir)

    # Temporarily only want to use Devon MSOAs
    devon_msoas = pd.read_csv(os.path.join(data_dir, "devon_msoas.csv"), header=None, names=["x", "y", "Num", "Code", "Desc"])
    m = Microsim(study_msoas=list(devon_msoas.Code), data_dir=data_dir)

    # Temporily use dummy data for testing
    #data_dir = os.path.join(base_dir, "dummy_data")
    #m = Microsim(data_dir=data_dir, testing=True)
    
    # save initial m
    output_dir = os.path.join(data_dir, "output")
    pickle_out = open(os.path.join(output_dir, "m0.pickle"),"wb")
    pickle.dump(m, pickle_out)
    pickle_out.close()

    # collect disease status in new df (for analysis/visualisation)
    individuals_to_pickle = m.individuals
    individuals_to_pickle["DiseaseStatus0"] = m.individuals.Disease_Status
    
    # collect location dangers at time 0 in new df(for analysis/visualisation)
    # TODO make a function for this so that it doesn't need to be repeated in the for loop below
    for name in m.activity_locations:
        # Get the details of the location activity
        activity = m.activity_locations[name]  # Pointer to the ActivityLocation object
        loc_name = activity.get_name()  # retail, school etc
        loc_ids = activity.get_ids()  # List of the IDs of the locations 
        loc_dangers = activity.get_dangers()  # List of the current dangers

        locals()[loc_name+'_to_pickle'] = pd.DataFrame(list(zip(loc_ids, loc_dangers)), columns=['ID', 'Danger0'])


    # Step the model
    for i in range(num_iter):
        m.step()
        
        # add to items to pickle
        individuals_to_pickle["DiseaseStatus"+str(i+1)] = m.individuals.Disease_Status
        for name in m.activity_locations:
            # Get the details of the location activity
            activity = m.activity_locations[name]  # Pointer to the ActivityLocation object
            loc_name = activity.get_name()  # retail, school etc
            loc_ids = activity.get_ids()  # List of the IDs of the locations 
            loc_dangers = activity.get_dangers()  # List of the current dangers

            locals()[loc_name+'_to_pickle']["Danger"+str(i+1)] = loc_dangers

    
    # save individuals and danger dfs
    for name in m.activity_locations:
        # Get the details of the location activity
        activity = m.activity_locations[name]  # Pointer to the ActivityLocation object
        loc_name = activity.get_name()  # retail, school etc
        
        
    return individuals_to_pickle 