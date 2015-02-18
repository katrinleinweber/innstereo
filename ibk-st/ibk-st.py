#!/usr/bin/python3

from gi.repository import Gtk, GdkPixbuf
from matplotlib.backends.backend_gtk3cairo import (FigureCanvasGTK3Cairo
    as FigureCanvas)
from matplotlib.backends.backend_gtk3 import (NavigationToolbar2GTK3 
    as NavigationToolbar)
import mplstereonet
import numpy as np
from numpy import sin, sqrt

#Internal imports
from dataview_classes import (PlaneDataView, LineDataView,
                             FaultPlaneDataView, SmallCircleDataView)
from layer_view import LayerTreeView
from layer_types import PlaneLayer, FaultPlaneLayer, LineLayer, SmallCircleLayer
from dialog_windows import (AboutDialog, PrintDialog, LayerProperties, 
                           StereonetProperties, FileChooserParse)
from plot_control import PlotSettings
from polar_axes import NorthPolarAxes
from file_parser import FileParseDialog

class MainWindow(object):
    def __init__(self, builder):
        """
        Initializes the main window and connects different functions.
        """
        global startup
        self.main_window = builder.get_object("main_window")
        self.sw_plot = builder.get_object("scrolledwindow1")
        self.sw_layer = builder.get_object("scrolledwindow2")
        self.sw_data = builder.get_object("scrolledwindow3")
        self.tb1 = builder.get_object("toolbar1")
        self.statbar = builder.get_object("statusbar1")
        self.plot_menu = builder.get_object("menu_plot_views")

        context = self.tb1.get_style_context()
        context.add_class(Gtk.STYLE_CLASS_PRIMARY_TOOLBAR)

        #Set up default options class
        self.settings = PlotSettings()

        #Set up layer view and connect signals
        self.layer_store = Gtk.TreeStore(bool, GdkPixbuf.Pixbuf, str, object)
        self.layer_view = LayerTreeView(self.layer_store)
        self.sw_layer.add(self.layer_view)
        
        #Connect signals of layer view
        self.layer_view.renderer_name.connect("edited", self.layer_name_edited)
        self.layer_view.renderer_activate_layer.connect("toggled", 
            self.on_layer_toggled)
        self.layer_view.connect("row-activated", self.layer_row_activated)
        self.select = self.layer_view.get_selection()
        self.select.connect("changed", self.layer_selection_changed)
        self.draw_features = False

        #Set up the plot
        self.fig = self.settings.get_fig()
        self.canvas = FigureCanvas(self.fig)
        self.sw_plot.add_with_viewport(self.canvas)
        self.ax_stereo = self.settings.get_stereonet()
        self.inv = self.settings.get_inverse_transform()
        self.view_mode = "stereonet"
        self.view_changed = False
        self.ax_rose = None

        #Set up event-handlers
        self.canvas.mpl_connect('motion_notify_event', 
            self.update_cursor_position)
        self.canvas.mpl_connect('button_press_event',
            self.mpl_canvas_clicked)

        self.redraw_plot()
        self.main_window.show_all()
    
    def on_menuitem_stereo_activate(self, widget):
        """
        Triggered from the menu bar. If the canvas is in a different view mode
        it switches to stereonet-only.
        """
        if self.view_mode != "stereonet":
            self.view_changed = True
            self.view_mode = "stereonet"
            self.redraw_plot()

    def on_menuitem_stereo_rose_activate(self, widget):
        """
        Triggered from the menu bar. If the canvas is in a different view mode
        it will be switched to a combined stereonet and rose diagram view.
        """
        if self.view_mode != "stereo-rose":
            self.view_changed = True
            self.view_mode = "stereo_rose"
            self.redraw_plot()

    def on_menuitem_rose_view_activate(self, widget):
        """
        Triggered from the menu bar. If the canvas is in a different view mode
        it will be switched to a rose diagram only view.
        """
        if self.view_mode != "rose":
            self.view_changed = True
            self.view_mode = "rose"
            self.redraw_plot()

    def on_menuitem_pt_view_activate(self, widget):
        """
        Triggered from the menu bar. If the canvas is in a different view mode
        it switches to the PT-View.
        """
        if self.view_mode != "pt":
            self.view_changed = True
            self.view_mode = "pt"
            self.redraw_plot()

    def on_toolbutton_rose_dataset_clicked(self, widget):
        """
        Triggered by the GUI. Copies the selected datasets to the rose diagram
        group of the layer treeview. The dataview is relinked so data changes
        are still registered.
        """
        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()

        for row in row_list:
            r = model[row]
            self.layer_store.append(None, [r[0], r[1], r[2], r[3]])

    def on_toolbutton_eigenvector_clicked(self, widget):
        """
        __!!__Implemented in mplstereonet now, change this function!
        """
        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()

        total_dipdir = []
        total_dip = []
        for row in row_list:
            layer_obj = model[row][3]
            dipdir, dip, sense = self.parse_lines(
                                                layer_obj.get_data_treestore())
            for x in dipdir:
                total_dipdir.append(x)
            for y in dip:
                total_dip.append(y)

        fit_strike1, fit_dip1 = mplstereonet.fit_girdle(total_dipdir, total_dip)
        fit_strike2, fit_dip2 = mplstereonet.fit_pole(total_dipdir, total_dip)
        fit_strike3, fit_dip3 = mplstereonet.analysis._sd_of_eigenvector(
                                                   [total_dipdir, total_dip], 1)

        store = self.add_layer_dataset("line")
        self.add_linear_feature(store, fit_strike1 + 180, 90 - fit_dip1)
        self.add_linear_feature(store, fit_strike2 + 180, 90 - fit_dip2)
        self.add_linear_feature(store, fit_strike3 + 180, 90 - fit_dip3)
        self.redraw_plot()
        
    def on_toolbutton_new_project_clicked(self, widget):
        """
        Triggered from the GUI. When the "new project"-button is pressed
        this function runs the startup function and creates a new and
        independent instance of the GUI.
        """
        startup()

    def on_menuitem_new_window_activate(self, widget):
        """
        Triggered from the menu bar: "File -> New". Opens a new independent
        window by calling the global startup function.
        """
        startup()

    def on_toolbutton_poles_to_lines_clicked(self, widget):
        """
        Checks if selected layers are planes or faultplanes. Copies the
        dip-direction - dip data into a line-dataset. If many layers are
        selected the data will be merged into one layer.
        """
        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()

        def iterate_over_data(model, path, itr, n):
            r = model[path]
            self.add_linear_feature(n, 180 + r[0], 90 - r[1])
        
        for row in row_list:
            layer_obj = model[row][3]
            
            if layer_obj == None:
                return
            else:
                layer_type = layer_obj.get_layer_type()

            if layer_type == "line":
                return

        #n = new datastore
        n = self.add_layer_dataset("line")

        for row in row_list:
            layer_obj = model[row][3]
            datastore = layer_obj.get_data_treestore()
            datastore.foreach(iterate_over_data, n)

        self.redraw_plot()

    def on_toolbutton_save_clicked(self, widget):
        """
        Triggered from the GUI. Saves the project.
        """
        raise NotImplementedError

    def on_toolbutton_show_table_clicked(self, widget):
        """
        Opens a new dialog window that makes it easier to view and filter the
        data of one layer.
        """
        raise NotImplementedError

    def on_toolbutton_delete_layer_clicked(self, widget):
        """
        Triggered when the "remove layers" toolbutton is pressed. Deletes all
        selected layers.
        __!!__ Currently has no warning message. What happens to data?
        """
        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()

        for row in reversed(row_list):
            itr = model.get_iter(row)
            model.remove(itr)

        selection.unselect_all()
        self.redraw_plot()

    def on_toolbutton_plot_properties_clicked(self, widget):
        """
        Triggered when the toolbutton is pressed. Creates and instance of the
        StereonetProperties class, which is a Gtk DialogWindow and runs it.
        """
        plot_properties = StereonetProperties(self.settings, self.redraw_plot)
        plot_properties.run()

    def on_toolbutton_print_figure_clicked(self, widget):
        """
        Triggered fromt the GUI. This function creates an instance of the
        GtkPrintUnixDialog and runs it.
        """
        raise NotImplementedError
        #print_dialog = PrintDialog()
        #print_dialog.run()

    def on_toolbutton_save_figure_clicked(self, widget):
        """
        Opens the matplotlib dialog window that allows saving the current figure
        in a specified location, name and file format.
        """
        nav = NavigationToolbar(self.canvas, self.main_window)
        nav.save_figure()

    def layer_view_clicked(self, treeview, button):
        """
        Called when one clicks with the mouse on the layer-treeview.
        Unselects all selected layers.
        """
        selection = self.layer_view.get_selection()
        selection.unselect_all()

    def on_toolbutton_draw_features_toggled(self, widget):
        """
        Activated when the toggle button is pressed. When self.draw_features
        is True then clicking on the canvas with an active layer will draw
        a features at that point.
        """
        if self.draw_features == False:
            self.draw_features = True
        else:
            self.draw_features = False

    def on_toolbutton_best_plane_clicked(self, widget):
        """
        Finds the optimal plane for a set of linears.
        """
        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()

        #Check if all selected layers are planes or faultplanes.
        only_linears = True
        for row in row_list:
            layer_obj = model[row][3]
            if layer_obj.get_layer_type() == "plane":
                only_linears = False

        if only_linears == False:
            return

        total_dipdir = []
        total_dip = []
        for row in row_list:
            layer_obj = model[row][3]
            dipdir, dip, sense = self.parse_lines(
                                            layer_obj.get_data_treestore())
            for x in dipdir:
                total_dipdir.append(x)
            for y in dip:
                total_dip.append(y)

        fit_strike, fit_dip = mplstereonet.fit_girdle(total_dip, total_dipdir,
                                measurement = "lines")

        store = self.add_layer_dataset("plane")
        self.add_planar_feature(store, fit_strike + 90, fit_dip)
        self.redraw_plot()

    def on_toolbutton_plane_intersect_clicked(self, widget):
        """
        Gets the selected layers and calculates a best fitting plane for them.
        """
        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()

        #Check if all selected layers are planes or faultplanes.
        only_planes = True
        for row in row_list:
            layer_obj = model[row][3]
            if layer_obj.get_layer_type() == "line":
                only_planes = False

        if only_planes == False:
            return
        
        total_dipdir = []
        total_dip = []
        for row in row_list:
            layer_obj = model[row][3]
            strike, dipdir, dip = self.parse_planes(
                                            layer_obj.get_data_treestore())
            for x in strike:
                total_dipdir.append(x)
            for y in dip:
                total_dip.append(y)

        plane_strike, plane_dip = mplstereonet.fit_pole(
                                    total_dipdir, total_dip,
                                    measurement = "poles")

        plane_strike2, plane_dip2 = mplstereonet.fit_pole(
                                    *mplstereonet.pole(total_dipdir, total_dip),
                                    measurement = "poles")

        self.ax_stereo.line(plane_dip, plane_strike+90)
        self.ax_stereo.plane(plane_strike, plane_dip)
        self.ax_stereo.plane(plane_strike2, plane_dip2, color = "#ff0000")
        self.ax_stereo.pole(plane_strike, plane_dip)
        self.ax_stereo.pole(plane_strike2, plane_dip2)
        self.canvas.draw()

    def layer_row_activated(self, treeview, path, column):
        """
        Excecutes when a treeview row is double-clicked. This passes the
        treeview-object, the path (or row) as an integer and the
        TreeViewColumn-object to this function.
        """
        layer_obj = self.layer_store[path][3]
        if layer_obj != None:
            layer_prop = LayerProperties(layer_obj, self.redraw_plot)
            layer_prop.run()

    def layer_selection_changed(self, selection):
        """
        When the selection in the layer-view is changed to a layer containing
        data, then the data is displayed in the data-view. If more than one
        row is sected the data view is removed from the scrolled window.
        """
        model, row_list = selection.get_selected_rows()

        #If one row is selected show the data view, else don't show it
        if len(row_list) == 1:
            row = row_list[0]
            layer_object = model[row][3]
            child = self.sw_data.get_child()
            if layer_object == None:
                #If it has a child remove it
                if child != None:
                    self.sw_data.remove(child)
            #Else: not a group layer
            else:
                #Get the treeview
                treeview_object = layer_object.get_data_treeview()
                #If there is a child remove it
                if child != None:
                    self.sw_data.remove(child)
                #Add new treeview
                self.sw_data.add(treeview_object)
                self.main_window.show_all()
        else:
            child = self.sw_data.get_child()
            #If there is a child remove it
            if child != None:
                self.sw_data.remove(child)
            #Add new treeview
            self.main_window.show_all()

    def on_layer_toggled(self, widget, path):
        """
        If the layer is toggled the bool field is switched between
        True (visible) and False (invisible).
        """
        self.layer_store[path][0] = not self.layer_store[path][0]
        self.redraw_plot()

    def add_layer_dataset(self, layer_type):
        """
        Is called by the different "new layer" toolbuttons. If the number of
        selected rows are 0 or more than one, the layer is appended at the end.
        If just one row is selected, and the row is a group, then the new
        layer is created in that group. Otherwise it is added at the end of the
        same level as the selection.
        """
        store = None

        def add_layer(itr):
            if layer_type == "plane":
                store = Gtk.ListStore(float, float, str)
                view = PlaneDataView(store, self.redraw_plot)
                layer_obj = PlaneLayer(store, view)
            elif layer_type == "faultplane":
                store = Gtk.ListStore(float, float, float, float, str)
                view = FaultPlaneDataView(store, self.redraw_plot)
                layer_obj = FaultPlaneLayer(store, view)
            elif layer_type == "line":
                store = Gtk.ListStore(float, float, str)
                view = LineDataView(store, self.redraw_plot)
                layer_obj = LineLayer(store, view)
            elif layer_type == "smallcircle":
                store = Gtk.ListStore(float, float, float)
                view = SmallCircleDataView(store, self.redraw_plot)
                layer_obj = SmallCircleLayer(store, view)

            pixbuf = layer_obj.get_pixbuf()
            self.layer_store.append(itr,
                [True, pixbuf, layer_obj.get_label(), layer_obj])
            return store

        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()

        rows = len(row_list)
        if rows == 0 or rows > 1:
            store = add_layer(None)
        else:
            #If selected item is group, add to group, else: add to level
            row = row_list[0]
            layer_obj = model[row][3]
            selection_itr = model.get_iter(row_list[0])
            if layer_obj == None:
                store = add_layer(selection_itr)
            else:
                parent_itr = model.iter_parent(selection_itr)
                store = add_layer(parent_itr)

        return store

    def on_toolbutton_create_plane_dataset_clicked(self, widget):
        """
        When the toolbutton "toolbutton_create_dataset" is pressed this function
        creates a new dataset in the currently active layer group.
        Each dataset has a corresponding data sheet.
        """
        self.add_layer_dataset("plane")

    def on_toolbutton_create_faultplane_dataset_clicked(self, widget):
        """
        When the toolbutton "toolbutton_create_dataset" is pressed this function
        creates a new dataset in the currently active layer group.
        Each dataset has a corresponding data sheet.
        """
        self.add_layer_dataset("faultplane")

    def on_toolbutton_create_line_dataset_clicked(self, widget):
        """
        Creates a new line data layer.
        """
        self.add_layer_dataset("line")

    def on_toolbutton_create_small_circle_clicked(self, widget):
        """
        Creates a new small cirlce layer.
        """
        self.add_layer_dataset("smallcircle")

    def parse_planes(self, treestore):
        """
        Parses planes and adds them to the plot. Parsing converts from dip
        direction to strikes.
        """
        strike = []
        dipdir = []
        dip = []
        for row in treestore:
            strike.append(float(row[0])-90)
            dipdir.append(float(row[0]))
            dip.append(float(row[1]))
        return strike, dipdir, dip

    def parse_faultplanes(self, treestore):
        """
        Parses a faultplane treestore. Converts planes from dip-direction to
        strikes so they can be plotted.
        """
        strike = []
        plane_dir = []
        plane_dip = []
        line_dir = []
        line_dip = []
        sense = []
        for row in treestore:
            strike.append(float(row[0]-90))
            plane_dir.append(float(row[0]))
            plane_dip.append(float(row[1]))
            line_dir.append(float(row[2]))
            line_dip.append(float(row[3]))
            sense.append(row[4])
        return strike, plane_dir, plane_dip, line_dir, line_dip, sense

    def parse_lines(self, treestore):
        """
        Parses linear data with the 3 columns dip direction, dip and sense.
        Returns a python-list for each column.
        """
        line_dir = []
        line_dip = []
        sense = []
        for row in treestore:
            line_dir.append(float(row[0]))
            line_dip.append(float(row[1]))
            sense.append(row[2])
        return line_dir, line_dip, sense

    def parse_smallcircles(self, treestore):
        """
        Parses small circle data. Data has 3 columns: Dip direction, dip and
        opening angle.
        """
        line_dir = []
        line_dip = []
        angle = []
        for row in treestore:
            line_dir.append(float(row[0]))
            line_dip.append(float(row[1]))
            angle.append(float(row[2]))
        return line_dir, line_dip, angle

    def draw_plane(self, layer_obj, dipdir, dip):
        """
        Function draws a great circle in the stereonet. It calls the formatting
        from the layer object.
        """
        self.ax_stereo.plane(dipdir, dip, color = layer_obj.get_line_color(),
                    label = layer_obj.get_label(),
                    linewidth = layer_obj.get_line_width(),
                    linestyle = layer_obj.get_line_style(),
                    dash_capstyle = layer_obj.get_capstyle(),
                    alpha = layer_obj.get_line_alpha(), clip_on = False)

    def draw_line(self, layer_obj, dipdir, dip):
        """
        Function draws a linear element in the stereonet. It calls the
        formatting from the layer object.
        """
        #ax.line takes dip first and then dipdir (as strike)
        self.ax_stereo.line(dip, dipdir, marker = layer_obj.get_marker_style(),
                    markersize = layer_obj.get_marker_size(),
                    color = layer_obj.get_marker_fill(),
                    label = layer_obj.get_label(),
                    markeredgewidth = layer_obj.get_marker_edge_width(),
                    markeredgecolor = layer_obj.get_marker_edge_color(),
                    alpha = layer_obj.get_marker_alpha(), clip_on = False)

    def draw_smallcircles(self, layer_obj, dipdir, dip, angle):
        """
        Function draws small circles in the stereonet. It calls the formatting
        from the layer object.
        """
        #ax.cone takes dip first and then dipdir!
        #facecolor needs to be "None" because there is a bug with which side to fill
        #Is not added to the legend yet. Matplotlib bug?
        self.ax_stereo.cone(dip, dipdir, angle, facecolor = "None",
                    color = layer_obj.get_line_color(),
                    linewidth = layer_obj.get_line_width(),
                    label = layer_obj.get_label(),
                    linestyle = layer_obj.get_line_style())

    def draw_poles(self, layer_obj, dipdir, dip):
        """
        Function draws a plane pole in the stereonet. It calls the formatting
        from the layer object.
        """
        self.ax_stereo.pole(dipdir, dip, marker = layer_obj.get_pole_style(),
                    markersize = layer_obj.get_pole_size(),
                    color = layer_obj.get_pole_fill(),
                    label = "Poles of {0}".format(layer_obj.get_label()),
                    markeredgewidth = layer_obj.get_pole_edge_width(),
                    markeredgecolor = layer_obj.get_pole_edge_color(),
                    alpha = layer_obj.get_pole_alpha(), clip_on = False)

    def draw_contours(self, layer_obj, dipdir, dips, measure_type):
        """
        MplStereonet accepts measurements as "poles" for planes and
        "lines" for linear measurements.
        """
        if len(dipdir) == 0:
            return None

        #Implement hatches = (['-', '+', 'x', '\\', '*', 'o', 'O', '.'])
        if layer_obj.get_draw_contour_fills() == True:
            cbar = self.ax_stereo.density_contourf(dipdir, dips,
                              measurement=measure_type,
                              method = layer_obj.get_contour_method(),
                              gridsize = layer_obj.get_contour_resolution(),
                              cmap = layer_obj.get_colormap(),
                              sigma = layer_obj.get_contour_sigma())
        else:
            cbar = None

        if layer_obj.get_draw_contour_lines() == True:
            if layer_obj.get_use_line_color() == True:
                clines = self.ax_stereo.density_contour(dipdir, dips,
                                measurement=measure_type,
                                method = layer_obj.get_contour_method(),
                                gridsize = layer_obj.get_contour_resolution(),
                                sigma = layer_obj.get_contour_sigma(),
                                colors = layer_obj.get_contour_line_color(),
                                linewidths = layer_obj.get_contour_line_width(),
                                linestyles = layer_obj.get_contour_line_style())
            else:
                clines = self.ax_stereo.density_contour(dipdir, dips,
                                measurement=measure_type,
                                method = layer_obj.get_contour_method(),
                                gridsize = layer_obj.get_contour_resolution(),
                                sigma = layer_obj.get_contour_sigma(),
                                cmap = layer_obj.get_colormap(),
                                linewidths = layer_obj.get_contour_line_width(),
                                linestyles = layer_obj.get_contour_line_style())                

        if layer_obj.get_draw_contour_labels() == True:
            if clines != None:
                self.ax_stereo.clabel(clines,
                                fontsize = layer_obj.get_contour_label_size())

        return cbar

    def redraw_plot(self, checkout_canvas = False):
        """
        This function is called after any changes to the datasets or when
        adding or deleting layer. The plot is cleared and then redrawn.
        layer[3] = layer object
        """
        if self.view_changed == True or checkout_canvas == True:
            self.view_changed = False
            if self.view_mode == "stereonet":
                self.inv = self.settings.get_inverse_transform()
                self.ax_stereo = self.settings.get_stereonet()
            elif self.view_mode == "stereo_rose":
                self.inv = self.settings.get_inverse_transform()
                self.ax_stereo, self.ax_rose = self.settings.get_stereo_rose()
            elif self.view_mode == "rose":
                self.inv = self.settings.get_inverse_transform()
                self.ax_rose = self.settings.get_rose_diagram()
            elif self.view_mode == "pt":
                self.inv = self.settings.get_inverse_transform()
                self.ax_stereo, self.ax_fluc, self.ax_mohr = (
                                            self.settings.get_pt_view())

        if self.view_mode == "stereonet":
            self.ax_stereo.cla()
        elif self.view_mode == "stereo_rose":
            self.ax_rose.cla()
            self.ax_stereo.cla()
        elif self.view_mode == "rose":
            self.ax_rose.cla()
        elif self.view_mode == "pt":
            self.ax_stereo.cla()
            self.ax_fluc.cla()
            self.ax_mohr.cla()

        if self.settings.get_draw_grid_state() == True:
            self.ax_stereo.grid(linestyle = self.settings.get_grid_linestyle(),
                                color = self.settings.get_grid_color(),
                                linewidth = self.settings.get_grid_width())

        deselected = []
        def iterate_over_rows(model, path, itr):
            layer_obj = model[path][3]
            if layer_obj != None:
                layer_type = layer_obj.get_layer_type()
                model[path][2] = layer_obj.get_label()
                model[path][1] = layer_obj.get_pixbuf()
            else:
                layer_type = "group"

            if model[path][0] == False:
                deselected.append(str(path))
                return
            
            draw = True
            for d in deselected:
                if str(path).startswith(d) == True:
                    draw = False

            if draw == False:
                return

            if layer_type == "plane":
                strike, dipdir, dip = self.parse_planes(
                                            layer_obj.get_data_treestore())
                if layer_obj.get_render_gcircles() == True:
                    self.draw_plane(layer_obj, strike, dip)
                if layer_obj.get_render_poles() == True:
                    self.draw_poles(layer_obj, strike, dip)
                self.draw_contours(layer_obj, strike, dip, "poles")

                num_bins = 360 / layer_obj.get_rose_spacing()
                bin_width = 2 * np.pi / num_bins
                dipdir = np.radians(dipdir)
                values, bin_edges = np.histogram(dipdir, num_bins,
                                                     range = (0, 2 * np.pi))

                if self.ax_rose != None:
                    self.ax_rose.bar(left = bin_edges[:-1], height = values,
                                     width = bin_width, alpha = 0.5,
                                     color = layer_obj.get_line_color(),
                                     edgecolor = layer_obj.get_pole_edge_color(),
                                     bottom = layer_obj.get_rose_bottom())

            if layer_type == "faultplane":
                strike, plane_dir, plane_dip, line_dir, line_dip, sense = (
                        self.parse_faultplanes(layer_obj.get_data_treestore()))
                self.draw_plane(layer_obj, strike, plane_dip)
                self.draw_line(layer_obj, line_dir, line_dip)

                if layer_obj.get_render_pole_contours() == True:
                    self.draw_contours(layer_obj, strike, plane_dip, "poles")
                else:
                    self.draw_contours(layer_obj, line_dip, line_dir, "lines")

            if layer_type == "line":
                dipdir, dip, sense = self.parse_lines(
                                         layer_obj.get_data_treestore())
                self.draw_line(layer_obj, dipdir, dip)
                self.draw_contours(layer_obj, dip, dipdir, "lines")

                num_bins = 360 / layer_obj.get_rose_spacing()
                bin_width = 2 * np.pi / num_bins
                dipdir = np.radians(dipdir)
                values, bin_edges = np.histogram(dipdir, num_bins,
                                                     range = (0, 2 * np.pi))

                if self.ax_rose != None:
                    self.ax_rose.bar(left = bin_edges[:-1], height = values,
                                     width = bin_width, alpha = 0.5,
                                     color = layer_obj.get_marker_fill(),
                                     edgecolor = layer_obj.get_marker_edge_color(),
                                     bottom = layer_obj.get_rose_bottom())

            if layer_type == "smallcircle":
                dipdir, dip, angle = self.parse_smallcircles(
                                        layer_obj.get_data_treestore())
                self.draw_smallcircles(layer_obj, dipdir, dip, angle)

        self.layer_store.foreach(iterate_over_rows)

        if self.settings.get_draw_legend() == True:
            handles, labels = self.ax_stereo.get_legend_handles_labels()
            newLabels, newHandles = [], []
            for handle, label in zip(handles, labels):
                if label not in newLabels:
                    newLabels.append(label)
                    newHandles.append(handle)
            if len(handles) != 0:
                self.ax_stereo.legend(newHandles, newLabels,
                                      bbox_to_anchor=(1.3, 1.1))
        self.canvas.draw()

    def on_toolbutton_create_group_layer_clicked(self, widget):
        """
        When the toolbutton "toolbutton_create_layer" is pressed this function
        calls the "add_layer"-function of the TreeStore. The called function
        creates a new layer-group at the end of the view.
        __!!__ Always adds to the top level.
        """
        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()
        same_depth = True

        def check_same_depth(rows):
            return rows[1:] == rows[:-1]

        #If no row is selected then the group is added to the end of the view
        if len(row_list) == 0:
            model.append(None,
                [True, self.settings.get_folder_icon(), "Layer Group", None])
        else:
            depth_list = []
            for row in row_list:
                itr = model.get_iter(row)
                depth_list.append(self.layer_store.iter_depth(itr))
                if check_same_depth(depth_list) == False:
                    same_depth = False
                    print("Selection is not on the same depth")
                    selection.unselect_all()
                    return

        def move_rows(parent_itr, itr):
            """
            Adds each row to the parent iter. First call is new group and 
            first row that was selected.
            Checks if it has children. If yes, it start a recursive loop.
            """
            #ov = old values
            ov = model[itr]
            new = model.append(parent_itr, [ov[0], ov[1], ov[2], ov[3]])
            children_left = model.iter_has_child(itr)
            while children_left == True:
                child = model.iter_children(itr)
                move_rows(new, child)
                model.remove(child)
                children_left = model.iter_has_child(itr)

        if same_depth == True and len(row_list) > 0:
            selection_itr = model.get_iter(row_list[0])
            parent_itr = model.iter_parent(selection_itr)
            new_group_itr = model.append(parent_itr,
                         [True, self.settings.get_folder_icon(),
                         "Layer group", None])
            for row in reversed(row_list):
                k = model[row]
                itr = model.get_iter(row)
                move_rows(new_group_itr, itr)
                model.remove(itr)

    def layer_name_edited(self, widget, path, new_label):
        """
        When the layer name is edited this function passes the new label to the
        TreeStore along with the correct path.
        """
        self.layer_store[path][2] = new_label
        layer_obj = self.layer_store[path][3]

        if layer_obj != None:
            layer_obj.set_label(new_label)

        self.redraw_plot()

    def on_menuitem_about_activate(self, widget):
        """
        Triggered when the menuitem "about" is pressed. Creates an instance
        of the AboutDialog class and calls the function "run" within that class
        to show the dialog.
        """
        about = AboutDialog()
        about.run()

    def on_menuitem_quit_activate(self, widget):
        """
        Triggered when the main window is closed from the menu. Terminates the
        Gtk main loop.
        """
        Gtk.main_quit()

    def on_main_window_destroy(self, widget):
        """
        Triggered when the main window is closed with the x-Button.
        Terminates the Gtk main loop
        """
        Gtk.main_quit()

    def on_toolbutton_remove_feature_clicked(self, widget):
        """
        Triggered when the toolbutton "remove feature" is clicked. Removes all
        the selected data rows from the currently active layer.
        """
        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()

        if len(row_list) == 1:
            row = row_list[0]
            data_treeview = model[row][3].get_data_treeview()
            data_treestore = model[row][3].get_data_treestore()
            data_selection = data_treeview.get_selection()
            data_model, data_row_list = data_selection.get_selected_rows()
            treeiter_list = []

            for p in reversed(data_row_list):
                itr = data_model.get_iter(p)
                data_treestore.remove(itr)

            data_selection.unselect_all()

        self.redraw_plot()

    def convert_xy_to_dirdip(self, event):
        """
        Converts xy-coordinates of a matplotlib-event into dip-direction/dip
        by using the inverse transformation of mplstereonet. Returns floats in
        degree.
        """
        alpha = np.arctan2(event.xdata, event.ydata)
        alpha_deg = np.degrees(alpha)
        if alpha_deg < 0:
            alpha_deg += 360

        xy = np.array([[event.xdata, event.ydata]])
        xy_trans = self.inv.transform(xy)

        x = float(xy_trans[0,0:1])
        y = float(xy_trans[0,1:2])

        array = mplstereonet.stereonet_math._rotate(np.degrees(x),
                    np.degrees(y), (-1)*alpha_deg)

        gamma = float(array[1])
        gamma_deg = 90 - np.degrees(gamma)
        return alpha_deg, gamma_deg

    def add_planar_feature(self, datastore, dip_direct=0, dip=0, sense=""):
        """
        Adds a planar feature row. Defaults to an empty row unless a dip
        direction and dip are given.
        """
        datastore.append([dip_direct, dip, sense])

    def add_linear_feature(self, datastore, dip_direct=0, dip=0, sense=""):
        """
        Adds a linear feature row. Defaults to an empty row unless a dip
        direction and dip are given.
        """
        datastore.append([dip_direct, dip, sense])

    def add_faultplane_feature(self, datastore, dip_direct = 0, dip = 0,
                               ldip_direct = 0, ldip = 0, sense = ""):
        """
        Adds a faultplane feature at the 
        """
        datastore.append([dip_direct, dip, ldip_direct, ldip, sense])

    def add_smallcircle_feature(self, datastore, dip_direct=0, dip=0,
                                angle=10):
        """
        Adds a small circle feature row. Defaults to an empty row unless a dip
        direction and dip are given.
        """
        datastore.append([dip_direct, dip, angle])

    def on_toolbutton_add_feature_clicked(self, widget):
        """
        Adds an empty row to the currently selected data layer.
        """
        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()

        if len(row_list) == 1:
            layer = row_list[0]
            current = model[layer][3]
            data_treestore = current.get_data_treestore()
            if data_treestore != None:
                layer_type = current.get_layer_type()
                if layer_type == "plane":
                    self.add_planar_feature(data_treestore)
                if layer_type == "line":
                    self.add_linear_feature(data_treestore)
                if layer_type == "faultplane":
                    self.add_faultplane_feature(data_treestore)
                if layer_type == "smallcircle":
                    self.add_smallcircle_feature(data_treestore)

    def mpl_canvas_clicked(self, event):
        """
        If the edit mode is off, clicking anywhere on the mpl canvas should
        deselect the layer treeview.
        If the edit mode is on the layer should stay selected and each
        click should draw a feature.
        """
        selection = self.layer_view.get_selection()
        if event.inaxes != None:
            if self.draw_features == False:
                selection.unselect_all()
                return

            selection = self.layer_view.get_selection()
            model, row_list = selection.get_selected_rows()

            if len(row_list) == 1:
                if event.inaxes != None:
                    alpha_deg, gamma_deg = self.convert_xy_to_dirdip(event)
            else:
                selection.unselect_all()
                return
            
            layer = row_list[0]
            current = model[layer][3]
            data_treestore = current.get_data_treestore()

            if data_treestore != None:
                layer_type = current.get_layer_type()
                if layer_type == "plane":
                    self.add_planar_feature(data_treestore, alpha_deg,
                                            gamma_deg)
                if layer_type == "line":
                    self.add_linear_feature(data_treestore, alpha_deg,
                                            gamma_deg)
                if layer_type == "faultplane":
                    self.add_faultplane_feature(data_treestore, alpha_deg,
                                            gamma_deg)
                if layer_type == "smallcircle":
                    self.add_smallcircle_feature(data_treestore, alpha_deg,
                                            gamma_deg)
                self.redraw_plot()

    def update_cursor_position(self, event):
        """
        When the mouse cursor hovers inside the plot, the position of the
        event is pushed to the statusbar at the bottom of the GUI.
        """
        if event.inaxes != None:
            alpha_deg, gamma_deg = self.convert_xy_to_dirdip(event)

            alpha_deg = int(alpha_deg)
            gamma_deg = int(gamma_deg)

            #Ensure 000/00 formatting
            alpha_deg = str(alpha_deg).rjust(3, "0")
            gamma_deg = str(gamma_deg).rjust(2, "0")

            self.statbar.push(1, ("{0} / {1}".format(alpha_deg, gamma_deg)))

    def on_toolbutton_file_parse_clicked(self, toolbutton):
        """
        Triggered from the GUI. Opens the filechooserdialog for parsing text
        files.
        """
        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()

        if len(row_list) == 1:
            fc = FileChooserParse(self.run_file_parser)
            fc.run()

    def run_file_parser(self, text_file):
        """
        Triggered when a file is opend from the filechooserdialog for parsing
        files. Passes the file to the file parsing dialog.
        """
        selection = self.layer_view.get_selection()
        model, row_list = selection.get_selected_rows()

        if len(row_list) == 1:
            row = row_list[0]
            layer_obj = model[row][3]
            fp = FileParseDialog(text_file, layer_obj, self.redraw_plot,
                                 self.add_planar_feature,
                                 self.add_linear_feature,
                                 self.add_faultplane_feature)
            fp.run()

def startup():
    """
    Initializes an instance of the Gtk.Builder and loads the GUI from the
    ".glade" file. Then it initializes the main window and starts the Gtk.main
    loop. This function is also passed to the window, so it can open up new
    instances of the program.
    """
    builder = Gtk.Builder()
    objects = builder.add_objects_from_file("gui_layout.glade",
         ("main_window", "image_new_plane", "image_new_faultplane",
         "image_new_line", "image_new_fold", "image_plane_intersect",
         "image_best_fitting_plane", "layer_right_click_menu",
         "image_create_small_circle", "menu_plot_views", "image_eigenvector",
         "poles_to_lines"))

    window_instance = MainWindow(builder)
    builder.connect_signals(window_instance)
    Gtk.main()

if __name__ == "__main__":
    startup()
