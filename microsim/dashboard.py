# -*- coding: utf-8 -*-
"""
Created on Thu Jun  4 16:22:57 2020

@author: Natalie
"""

import os
import pickle
import pandas as pd
import numpy as np
import geopandas as gpd
import imageio
from shapely.geometry import Point
import json

from bokeh.io import output_file
from bokeh.plotting import figure, show
from bokeh.models import (BasicTicker, CDSView, ColorBar, ColumnDataSource,
                          CustomJS, CustomJSFilter, 
                          GeoJSONDataSource, HoverTool, Legend,
                          LinearColorMapper, PrintfTickFormatter, Slider)
from bokeh.layouts import row, column, gridplot, grid, widgetbox
from bokeh.models.widgets import Tabs, Panel
from bokeh.palettes import brewer
from bokeh.transform import transform



# Functions
# ---------

def create_venue_dangers_dict():
    '''
    Reads in venue pickle files (venues from locations_dict) and populates dangers_dict_3d (raw data: venue, day, run), dangers_dict (mean across runs) and dangers_dict_std (standard deviation across runs)
    '''
    
    for key, value in locations_dict.items():
        for r in range(nr_runs):
            data_file = os.path.join(data_dir, "output",f"{r}",f"{locations_dict[key]}.pickle")
            pickle_in = open(data_file,"rb")
            dangers = pickle.load(pickle_in)
            pickle_in.close()
            # set row index to ID
            dangers.set_index('ID', inplace = True)
            dangers_colnames = dangers.columns
            dangers_rownames = dangers.index
            if r == 0:
                dangers_3d = np.zeros((dangers.shape[0],dangers.shape[1],nr_runs))        
            dangers_3d[:,:,r] = dangers.values
        dangers_dict_3d[key] = dangers_3d
        dangers_dict[key] = pd.DataFrame(data=dangers_3d.mean(axis=2), index=dangers_rownames, columns=dangers_colnames)
        dangers_dict_std[key] = pd.DataFrame(data=dangers_3d.std(axis=2), index=dangers_rownames, columns=dangers_colnames)


def create_msoa_dangers_dict():
    '''
    Converts dangers_dict to MSOA level data for the appropriate venue types. Produces average danger score (sum dangers in MSOA / total nr venues in MSOA)
    '''
    
    for key in ['Retail','PrimarySchool','SecondarySchool']:
        dangers = dangers_dict[key]
        if key == 'Retail':
            msoa_code = retail.MSOA_code
        else:
            msoa_code = schools.MSOA_code
        dangers['MSOA'] = msoa_code
        # count nr for this condition per area
        msoa_sum = dangers.groupby(['MSOA']).agg('sum')  
        msoa_count = dangers.groupby(['MSOA']).agg('count')  
        msoa_avg =  msoa_sum.div(msoa_count, axis='index')
        dangers_msoa_dict[key] = msoa_avg
        

def create_counts_dict_3d():
    '''
    Counts per condition. Produces 3 types of counts:
    msoacounts: nr per msoa and day
    totalcounts: nr per day (across all areas)
    cumcounts: nr per MSOA (across given time period)
    '''
    
    for r in range(nr_runs):
        # read in pickle file individuals (disease status)
        data_file = os.path.join(data_dir, "output", f"{r}", "Individuals.pickle")
        pickle_in = open(data_file,"rb")
        individuals_tmp = pickle.load(pickle_in)
        pickle_in.close()
        # if first ever run, keep copy and initialise 3D frame for aggregating
        if r == 0:
            individuals = individuals_tmp
            #msoas = sorted(individuals.area.unique())
            msoas.extend(sorted(individuals.area.unique()))
            area_individuals = individuals['area']
            start_col = individuals.shape[1] - nr_days + start_day
            end_col = individuals.shape[1] - nr_days + end_day + 1
            uniqcounts = np.zeros((individuals.shape[0],len(conditions_dict),nr_runs))   # empty dictionary to store results: total nr, each person assigned to highest condition: 0<1<2<(3==4)           
            
            # # to normalise counts, need to know total nr people per MSOA
            # nrpeople_msoa = individuals[['ID','area']].groupby(['area']).count()
            # nrpeople_msoa.rename(columns={'ID':'nr_people'}, inplace=True)
            
        for key, value in conditions_dict.items():
            if r == 0:
                msoacounts_dict_3d[key] = np.zeros((len(msoas),nr_days,nr_runs))        
                totalcounts_dict_3d[key] = np.zeros((nr_days,nr_runs))  
                cumcounts_dict_3d[key] = np.zeros((len(msoas),nr_runs))
            # cumulative counts
            # select right columns
            tmp = individuals_tmp.iloc[:,start_col:end_col]  
            # find all rows with condition (dict value)
            indices = tmp[tmp.eq(value).any(1)].index
            # create new df of zeros and replace with 1 at indices
            cumcounts_run = pd.DataFrame(np.zeros((tmp.shape[0], 1)))
            cumcounts_run.loc[indices] = 1
            uniqcounts[:,value,r] = cumcounts_run.values[:,0]
            # merge with MSOA df
            cumcounts_run = cumcounts_run.merge(area_individuals, left_index=True, right_index=True)
            cumcounts_msoa_run = cumcounts_run.groupby(['area']).sum()
            cumcounts_msoa_run = cumcounts_msoa_run.values
    
            # loop aroud days
            msoacounts_run = np.zeros((len(msoas),nr_days))
            for d in range(0, nr_days):
                # count nr for this condition per area
                msoa_count_temp = individuals_tmp[individuals_tmp.iloc[:, -nr_days+d] == conditions_dict[key]].groupby(['Area']).agg({individuals_tmp.columns[-nr_days+d]: ['count']})  
                msoa_count_temp = msoa_count_temp.values
                # add new column
                msoacounts_run[:,d] = msoa_count_temp[:, 0]
                
            # get current values from dict
            msoacounts = msoacounts_dict_3d[key]
            totalcounts = totalcounts_dict_3d[key]
            cumcounts = cumcounts_dict_3d[key]
            # add current run's values
            msoacounts[:,:,r] = msoacounts_run
            totalcounts[:,r] = msoacounts_run.sum(axis=0)
            cumcounts[:,r] = cumcounts_msoa_run[:, 0]
            # write out to dict
            msoacounts_dict_3d[key] = msoacounts
            totalcounts_dict_3d[key] = totalcounts
            cumcounts_dict_3d[key] = cumcounts

   
def create_counts_dict_mean_std():
    '''
    Summarize data in 3d counts dictionaries into mean and standard deviation across runs
    '''
    
    for key, value in conditions_dict.items():
        # get current values from dict
        msoacounts = msoacounts_dict_3d[key]
        totalcounts = totalcounts_dict_3d[key]
        cumcounts = cumcounts_dict_3d[key]
        # aggregate
        msoacounts_std = msoacounts.std(axis=2)
        msoacounts = msoacounts.mean(axis=2)
        totalcounts_std = totalcounts.std(axis=1)
        totalcounts = totalcounts.mean(axis=1)
        cumcounts_std = cumcounts.std(axis=1)
        cumcounts = cumcounts.mean(axis = 1)
        # write out to dict
        msoacounts_dict[key] = pd.DataFrame(data=msoacounts, index=msoas, columns=dict_days)
        msoacounts_dict_std[key] = pd.DataFrame(data=msoacounts_std, index=msoas, columns=dict_days)
        totalcounts_dict[key] = pd.Series(data=totalcounts, index=dict_days)
        totalcounts_dict_std[key] = pd.Series(data=totalcounts_std, index=dict_days)
        cumcounts_dict[key] = pd.Series(data=cumcounts, index=msoas)
        cumcounts_dict_std[key] = pd.Series(data=cumcounts_std, index=msoas)
     
        
    # TO BE COMPLETED - calculate unique nr people per condition 
    # uniqcounts_dict_3d = {}
    # for r in range(nr_runs):
    #     tmp = uniqcounts[:,4,r]
    #     result = np.where(tmp == 1)
    #     indices = result[0]
    #     # set other values on row to zero
    
    
# plot 1a: heatmap condition

def plot_heatmap_condition(condition2plot):
    """ Create heatmap plot: x axis = time, y axis = MSOAs, colour = nr people with condition = condition2plot. condition2plot is key to conditions_dict."""
    
    # Prep data
    var2plot = msoacounts_dict[condition2plot]
    var2plot = var2plot.rename_axis(None, axis=1).rename_axis('MSOA', axis=0)
    var2plot.columns.name = 'Day'
    # reshape to 1D array or rates with a month and year for each row.
    df_var2plot = pd.DataFrame(var2plot.stack(), columns=['condition']).reset_index()
    source = ColumnDataSource(df_var2plot)
    # add better colour 
    mapper_1 = LinearColorMapper(palette=colours_ch_cond[condition2plot], low=0, high=var2plot.max().max())
    # create fig
    s1 = figure(title="Heatmap",
               x_range=list(var2plot.columns), y_range=list(var2plot.index), x_axis_location="above")
    s1.rect(x="Day", y="MSOA", width=1, height=1, source=source,
           line_color=None, fill_color=transform('condition', mapper_1))
    color_bar_1 = ColorBar(color_mapper=mapper_1, location=(0, 0), orientation = 'horizontal', ticker=BasicTicker(desired_num_ticks=len(colours_ch_cond[condition2plot])))
    s1.add_layout(color_bar_1, 'below')
    s1.axis.axis_line_color = None
    s1.axis.major_tick_line_color = None
    s1.axis.major_label_text_font_size = "7px"
    s1.axis.major_label_standoff = 0
    s1.xaxis.major_label_orientation = 1.0
    # Create hover tool
    s1.add_tools(HoverTool(
        tooltips=[
            ( f'Nr {condition2plot}',   '@condition'),
            ( 'Day',  '@Day' ), 
            ( 'MSOA', '@MSOA'),
        ],
    ))
    s1.toolbar.autohide = False
    plotref_dict[f"hm{condition2plot}"] = s1    
    
    
# plot 1b: heatmap venue

def plot_heatmap_danger(venue2plot):
    """ Create heatmap plot: x axis = time, y axis = MSOAs, colour =danger score. """
    
    # Prep data
    var2plot = dangers_msoa_dict[venue2plot]
    var2plot.columns.name = 'Day'
    # reshape to 1D array or rates with a month and year for each row.
    df_var2plot = pd.DataFrame(var2plot.stack(), columns=['venue']).reset_index()
    source = ColumnDataSource(df_var2plot)
    # add better colour 
    mapper_1 = LinearColorMapper(palette=colours_ch_danger, low=0, high=var2plot.max().max())
    # Create fig
    s1 = figure(title="Heatmap",
               x_range=list(var2plot.columns), y_range=list(var2plot.index), x_axis_location="above")
    s1.rect(x="Day", y="MSOA", width=1, height=1, source=source,
           line_color=None, fill_color=transform('venue', mapper_1))
    color_bar_1 = ColorBar(color_mapper=mapper_1, location=(0, 0), orientation = 'horizontal', ticker=BasicTicker(desired_num_ticks=len(colours_ch_danger)))
    s1.add_layout(color_bar_1, 'below')
    s1.axis.axis_line_color = None
    s1.axis.major_tick_line_color = None
    s1.axis.major_label_text_font_size = "7px"
    s1.axis.major_label_standoff = 0
    s1.xaxis.major_label_orientation = 1.0
    # Create hover tool
    s1.add_tools(HoverTool(
        tooltips=[
            ( 'danger score',   '@venue'),
            ( 'Day',  '@Day' ), 
            ( 'MSOA', '@MSOA'),
        ],
    ))
    s1.toolbar.autohide = False
    plotref_dict[f"hm{venue2plot}"] = s1    
    
    
# plot 2: disease conditions across time

def plot_cond_time():
    # build ColumnDataSource
    data_s2 = dict(totalcounts_dict)
    data_s2["days"] = days
    for key, value in totalcounts_dict.items():
        data_s2[f"{key}_std_upper"] = totalcounts_dict[key] + totalcounts_dict_std[key]
        data_s2[f"{key}_std_lower"] = totalcounts_dict[key] - totalcounts_dict_std[key]
    source_2 = ColumnDataSource(data=data_s2)
    # Create fig
    s2 = figure(background_fill_color="#fafafa",title="Time", x_axis_label='Time', y_axis_label='Nr of people',toolbar_location='above')
    legend_it = []
    for key, value in totalcounts_dict.items():
        c1 = s2.line(x = 'days', y = key, source = source_2, line_width=2, line_color=colour_dict[key],muted_color="grey", muted_alpha=0.2)   
        c2 = s2.square(x = 'days', y = key, source = source_2, fill_color=colour_dict[key], line_color=colour_dict[key], size=5, muted_color="grey", muted_alpha=0.2)
        # c3 = s2.rect('days', f"{key}_std_upper", 0.2, 0.01, source = source_2, line_color="black",muted_color="grey", muted_alpha=0.2)
        # c4 = s2.rect('days', f"{key}_std_lower", 0.2, 0.01, source = source_2, line_color="black",muted_color="grey", muted_alpha=0.2)
        c5 = s2.segment('days', f"{key}_std_lower", 'days', f"{key}_std_upper", source = source_2, line_color="black",muted_color="grey", muted_alpha=0.2)
        legend_it.append((f"nr {key}", [c1,c2,c5]))
    legend = Legend(items=legend_it)
    legend.click_policy="hide"
    # Misc
    tooltips = tooltips_cond_basic.copy()
    tooltips.append(tuple(( 'Day',  '@days' )))
    s2.add_tools(HoverTool(
        tooltips=tooltips,
    ))
    s2.add_layout(legend, 'right')
    s2.toolbar.autohide = False
    plotref_dict["cond_time"] = s2    
    
    
# plot 3: Conditions across MSOAs

def plot_cond_msoas():
    # build ColumnDataSource
    data_s3 = {}
    data_s3["msoa_nr"] = msoas_nr
    data_s3["msoa_name"] = msoas
    for key, value in cumcounts_dict.items():
        data_s3[key] = cumcounts_dict[key]
        data_s3[f"{key}_std_upper"] = cumcounts_dict[key] + cumcounts_dict_std[key]
        data_s3[f"{key}_std_lower"] = cumcounts_dict[key] - cumcounts_dict_std[key]
    source_3 = ColumnDataSource(data=data_s3)
    # Create fig
    s3 = figure(background_fill_color="#fafafa",title="MSOA", x_axis_label='Nr people', y_axis_label='MSOA',toolbar_location='above')
    legend_it = []
    for key, value in msoacounts_dict.items():
        c1 = s3.circle(x = key, y = 'msoa_nr', source = source_3, fill_color=colour_dict[key], line_color=colour_dict[key], size=5,muted_color="grey", muted_alpha=0.2)   
        c2 = s3.segment(f"{key}_std_lower", 'msoa_nr', f"{key}_std_upper", 'msoa_nr', source = source_3, line_color="black",muted_color="grey", muted_alpha=0.2)
        legend_it.append((key, [c1,c2]))
    legend = Legend(items=legend_it)
    legend.click_policy="hide"
    # Misc
    s3.yaxis.ticker = data_s3["msoa_nr"]
    MSOA_dict = dict(zip(data_s3["msoa_nr"], data_s3["msoa_name"]))
    s3.yaxis.major_label_overrides = MSOA_dict
    tooltips = tooltips_cond_basic.copy()
    tooltips.append(tuple(( 'MSOA',  '@msoa_name' )))
    s3.add_tools(HoverTool(
        tooltips=tooltips,
    ))
    s3.add_layout(legend, 'right')
    s3.toolbar.autohide = False
    plotref_dict["cond_msoas"] = s3


# plot 4a: choropleth

def plot_choropleth_condition_slider(condition2plot):
    # Prepare data
    max_val = 0
    merged_data = pd.DataFrame()
    merged_data["y"] = msoacounts_dict[condition2plot].iloc[:,0]
    for d in range(0,nr_days):
        merged_data[f"{d}"] = msoacounts_dict[condition2plot].iloc[:,d]
        max_tmp = merged_data[f"{d}"].max()
        if max_tmp > max_val: max_val = max_tmp
    merged_data["Area"] = msoacounts_dict[condition2plot].index.to_list()
    merged_data = pd.merge(map_df,merged_data,on='Area')
    geosource = GeoJSONDataSource(geojson = merged_data.to_json())
    # Create color bar
    mapper_4 = LinearColorMapper(palette = colours_ch_cond[condition2plot], low = 0, high = max_val)
    color_bar_4 = ColorBar(color_mapper = mapper_4, 
                          label_standoff = 8,
                          #"width = 500, height = 20,
                          border_line_color = None,
                          location = (0,0), 
                          orientation = 'horizontal')
    # Create figure object.
    s4 = figure(title = f"{condition2plot} total")
    s4.xgrid.grid_line_color = None
    s4.ygrid.grid_line_color = None
    # Add patch renderer to figure.
    msoasrender = s4.patches('xs','ys', source = geosource,
                        fill_color = {'field' : 'y',
                                      'transform' : mapper_4},     
                        line_color = 'gray', 
                        line_width = 0.25, 
                        fill_alpha = 1)
    # Create hover tool
    s4.add_tools(HoverTool(renderers = [msoasrender],
                           tooltips = [('MSOA','@Area'),
                                        ('Nr people','@y'),
                                         ]))
    s4.add_layout(color_bar_4, 'below')
    s4.axis.visible = False
    s4.toolbar.autohide = True
    # Slider 
    callback = CustomJS(args=dict(source=geosource), code="""
        var data = source.data;
        var f = cb_obj.value
        var y = data['y']
        var toreplace = data[f]
        for (var i = 0; i < y.length; i++) {
            y[i] = toreplace[i]
        }
        source.change.emit();
    """)
    slider = Slider(start=0, end=20, value=0, step=1, title="Day")
    slider.js_on_change('value', callback)
    plotref_dict[f"chpl{condition2plot}"] = s4
    plotref_dict[f"chsl{condition2plot}"] = slider
    
    
# plot 4b: choropleth dangers

def plot_choropleth_danger_slider(venue2plot):
    # Prep data
    max_val = 0
    merged_data = pd.DataFrame()
    merged_data["y"] = dangers_msoa_dict[venue2plot].iloc[:,0]
    for d in range(0,nr_days):
        merged_data[f"{d}"] = dangers_msoa_dict[venue2plot].iloc[:,d]
        max_tmp = merged_data[f"{d}"].max()
        if max_tmp > max_val: max_val = max_tmp    
    merged_data["Area"] = dangers_msoa_dict[venue2plot].index.to_list()
    merged_data = pd.merge(map_df,merged_data,on='Area')
    geosource2 = GeoJSONDataSource(geojson = merged_data.to_json())
    # Create color bar 
    mapper_4 = LinearColorMapper(palette = colours_ch_danger, low = 0, high = max_val)
    color_bar_4 = ColorBar(color_mapper = mapper_4, 
                          label_standoff = 8,
                          border_line_color = None,
                          location = (0,0), 
                          orientation = 'horizontal')
    # Create figure object
    s4 = figure(title = f"{venue2plot} total")
    s4.xgrid.grid_line_color = None
    s4.ygrid.grid_line_color = None
    # Add patch renderer to figure.
    msoasrender = s4.patches('xs','ys', source = geosource2,
                        fill_color = {'field' : 'y',
                                      'transform' : mapper_4},     
                        line_color = 'gray', 
                        line_width = 0.25, 
                        fill_alpha = 1)
    # Create hover tool
    s4.add_tools(HoverTool(renderers = [msoasrender],
                           tooltips = [('MSOA','@Area'),
                                        ('Danger score','@y'),
                                         ]))
    s4.add_layout(color_bar_4, 'below')
    s4.axis.visible = False
    s4.toolbar.autohide = True
    # Slider
    callback = CustomJS(args=dict(source=geosource2), code="""
        var data = source.data;
        var f = cb_obj.value
        var y = data['y']
        var toreplace = data[f]
        for (var i = 0; i < y.length; i++) {
            y[i] = toreplace[i]
        }
        source.change.emit();
    """)
    slider = Slider(start=0, end=20, value=0, step=1, title="Day")
    slider.js_on_change('value', callback)
    plotref_dict[f"chpl{venue2plot}"] = s4
    plotref_dict[f"chsl{venue2plot}"] = slider    


# plot 5: danger scores across time per venue type

def plot_danger_time():
    # build ColumnDataSource
    data_s5 = {}
    data_s5["days"] = days
    for key, value in dangers_dict.items():
        data_s5[key] = value.mean(axis = 0)
        data_s5[f"{key}_std_upper"] = value.mean(axis = 0) + value.std(axis = 0)
        data_s5[f"{key}_std_lower"] = value.mean(axis = 0) - value.std(axis = 0)
    source_5 = ColumnDataSource(data=data_s5)
    # Build figure
    s5 = figure(background_fill_color="#fafafa",title="Time", x_axis_label='Time', y_axis_label='Average danger score', toolbar_location='above')
    legend_it = []
    for key, value in dangers_dict.items():
        c1 = s5.line(x = 'days', y = key, source = source_5, line_width=2, line_color=colour_dict[key], muted_color="grey", muted_alpha=0.2)
        c2 = s5.circle(x = 'days', y = key, source = source_5, fill_color=colour_dict[key], line_color=colour_dict[key], size=5)
        c3 = s5.segment('days', f"{key}_std_lower", 'days', f"{key}_std_upper", source = source_5, line_color="black",muted_color="grey", muted_alpha=0.2)
        legend_it.append((key, [c1,c2,c3]))
    legend = Legend(items=legend_it)
    legend.click_policy="hide"
    # Misc
    tooltips = tooltips_venue_basic.copy()
    tooltips.append(tuple(( 'Day',  '@days' )))
    s5.add_tools(HoverTool(
        tooltips=tooltips,
    ))
    s5.add_layout(legend, 'right')
    s5.toolbar.autohide = False
    plotref_dict["danger_time"] = s5





# Set parameters
# --------------

# directory to read data from
base_dir = os.getcwd()  # get current directory (usually RAMP-UA)
data_dir = os.path.join(base_dir, "devon_data") # go to data dir

# dictionaries with condition and venue names
# conditions are coded as numbers in microsim output
conditions_dict = {
  "susceptible": 0,
  "presymptomatic": 1,
  "symptomatic": 2,
  "recovered": 3,
  "dead": 4,
}
# venues are coded as strings - redefined here so script works as standalone, could refer to ActivityLocations instead
locations_dict = {
  "PrimarySchool": "PrimarySchool",
  "SecondarySchool": "SecondarySchool",
  "Retail": "Retail",
  "Work": "Work",
  "Home": "Home",
}

# determine where/how the visualization will be rendered
html_output = os.path.join(data_dir, 'dashboard.html')
output_file(html_output, title='RAMP-UA microsim output') # Render to static HTML
#output_notebook()  # To tender inline in a Jupyter Notebook

# default list of tools for plots
tools = "crosshair,hover,pan,wheel_zoom,box_zoom,reset,box_select,lasso_select"

# colour schemes for plots
# colours for line plots
colour_dict = {
  "susceptible": "blue",
  "presymptomatic": "orange",
  "symptomatic": "red",
  "recovered": "green",
  "dead": "black",
  "Retail": "blue",
  "PrimarySchool": "orange",
  "SecondarySchool": "red",
  "Work": "black",
  "Home": "green",
}
# colours for heatmaps and choropleths for conditions (colours_ch_cond) and venues/danger scores (colours_ch_danger)
colours_ch_cond = {
  "susceptible": brewer['Blues'][8][::-1],
  "presymptomatic": brewer['YlOrRd'][8][::-1],
  "symptomatic": brewer['YlOrRd'][8][::-1],
  "recovered": brewer['Greens'][8][::-1],
  "dead": brewer['YlOrRd'][8][::-1],
}
colours_ch_danger = brewer['YlOrRd'][8][::-1]
# other good palettes / way to define:
# palette = brewer['BuGn'][8][::-1]    # -1 reverses the order
# palette = = ["#75968f", "#a5bab7", "#c9d9d3", "#e2e2e2", "#dfccce", "#ddb7b1", "#cc7878", "#933b41", "#550b1d"]


# Read in third party data
# ------------------------

# read in details about venues
data_file = os.path.join(data_dir, "devon-schools","exeter schools.csv")
schools = pd.read_csv(data_file)
data_file = os.path.join(data_dir, "devon-retail","devon smkt.csv")
retail = pd.read_csv(data_file)

# load in shapefile with England MSOAs for choropleth
sh_file = os.path.join(data_dir, "MSOAS_shp","bcc21fa2-48d2-42ca-b7b7-0d978761069f2020412-1-12serld.j1f7i.shp")
map_df = gpd.read_file(sh_file)
# rename column to get ready for merging
map_df.rename(index=str, columns={'msoa11cd': 'Area'},inplace=True)

# postcode to MSOA conversion (for retail data)
data_file = os.path.join(data_dir, "PCD_OA_LSOA_MSOA_LAD_AUG19_UK_LU.csv")
postcode_lu = pd.read_csv(data_file, encoding = "ISO-8859-1", usecols = ["pcds", "msoa11cd"])



# Read in and process pickled output from microsim
# ------------------------------------------------

# multiple runs: create mean and std dev
# we assume all directories in the ouput directory are runs (alternatively we could ask user to supply number of runs)
nr_runs = len(next(os.walk(os.path.join(data_dir, "output")))[1])  

# read in and process pickle files location/venue dangers
# start with empty dictionaries
dangers_dict = {}  # to store mean (value to be plotted)
dangers_dict_std = {}  # to store std (error bars)
dangers_dict_3d = {} # to store full 3D data
# fill dictionaries
create_venue_dangers_dict()
   
# how many days have we got
nr_days = dangers_dict["Retail"].shape[1]
days = [i for i in range(0,nr_days)]

# Add additional info about schools and retail including spatial coordinates
# merge
primaryschools = pd.merge(schools, dangers_dict["PrimarySchool"], left_index=True, right_index=True)
secondaryschools = pd.merge(schools, dangers_dict["SecondarySchool"], left_index=True, right_index=True)
retail = pd.merge(retail, dangers_dict["Retail"], left_index=True, right_index=True)

# creat LUT
lookup = dict(zip(postcode_lu.pcds, postcode_lu.msoa11cd)) # zip together the lists and make a dict from it
# use LUT and add column to retail variable
msoa_code = [lookup.get(retail.postcode[i]) for i in range(1, len(retail.postcode)+1, 1)]
retail.insert(2, 'MSOA_code', msoa_code)

# normalised danger scores per msoa for schools and retail (for choropleth)
dangers_msoa_dict = {}
create_msoa_dangers_dict()  

# counts per condition
# start with empty dictionaries
msoacounts_dict_3d = {}  # to store mean nr per msoa and day
totalcounts_dict_3d = {}  # empty dictionary to store results: nr per day
cumcounts_dict_3d = {}  # empty dictionary to store results: total nr across time period
# time range for cumulative counts
start_day = 0
end_day = nr_days-1 # last 'named' day - nrs starts from 0
msoas = []
create_counts_dict_3d()
    
dict_days = [] # empty list for column names 'Day0' etc
for d in range(0, nr_days):
    dict_days.append(f'Day{d}')
  
# aggregate counts across runs (means and std) from 3d data
msoacounts_dict = {}  
totalcounts_dict = {}
cumcounts_dict = {}
msoacounts_dict_std = {}  
totalcounts_dict_std = {}
cumcounts_dict_std = {}
create_counts_dict_mean_std()



# Plotting
# --------

# MSOA nrs (needs nrs not strings to plot)
msoas_nr = [i for i in range(0,len(msoas))]

# optional: threshold map to only use MSOAs currently in the study or selection
map_df = map_df[map_df['Area'].isin(msoas)]

# basic tool tip for condition plots
tooltips_cond_basic=[]
for key, value in totalcounts_dict.items():
    tooltips_cond_basic.append(tuple(( f"Nr {key}",   f"@{key}")))

tooltips_venue_basic=[]
for key, value in dangers_dict.items():
    tooltips_venue_basic.append(tuple(( f"Danger {key}",   f"@{key}")))
   
# empty dictionary to track condition and venue specific plots
plotref_dict = {}

# create heatmaps condition
for key,value in conditions_dict.items():
    plot_heatmap_condition(key)

# create heatmaps venue dangers
for key,value in dangers_msoa_dict.items():
    plot_heatmap_danger(key)
    
# disease conditions across time
plot_cond_time()

# disease conditions across msoas
plot_cond_msoas()    

# choropleth conditions
for key,value in conditions_dict.items():
    plot_choropleth_condition_slider(key)

# choropleth dangers
for key,value in dangers_msoa_dict.items():
    plot_choropleth_danger_slider(key)

# danger scores across time per venue type
plot_danger_time()   



# Layout and output

tab1 = Panel(child=row(plotref_dict["cond_time"], plotref_dict["cond_msoas"]), title='Summary conditions')

tab2 = Panel(child=row(plotref_dict["hmsusceptible"],column(plotref_dict["chslsusceptible"],plotref_dict["chplsusceptible"])), title='Susceptible')

tab3 = Panel(child=row(plotref_dict["hmpresymptomatic"],column(plotref_dict["chslpresymptomatic"],plotref_dict["chplpresymptomatic"])), title='Presymptomatic')

tab4 = Panel(child=row(plotref_dict["hmsymptomatic"],column(plotref_dict["chslsymptomatic"],plotref_dict["chplsymptomatic"])), title='Symptomatic')

tab5 = Panel(child=row(plotref_dict["hmrecovered"],column(plotref_dict["chslrecovered"],plotref_dict["chplrecovered"])), title='Recovered')

tab6 = Panel(child=row(plotref_dict["hmdead"],column(plotref_dict["chsldead"],plotref_dict["chpldead"])), title='Dead')

tab7 = Panel(child=row(plotref_dict["danger_time"]), title='Summary dangers')

tab8 = Panel(child=row(plotref_dict["hmRetail"],column(plotref_dict["chslRetail"],plotref_dict["chplRetail"])), title='Danger retail')

tab9 = Panel(child=row(plotref_dict["hmPrimarySchool"],column(plotref_dict["chslPrimarySchool"],plotref_dict["chplPrimarySchool"])), title='Danger primary school')

tab10 = Panel(child=row(plotref_dict["hmSecondarySchool"],column(plotref_dict["chslSecondarySchool"],plotref_dict["chplSecondarySchool"])), title='Danger secondary school')

# Put the Panels in a Tabs object
tabs = Tabs(tabs=[tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10])

show(tabs)
