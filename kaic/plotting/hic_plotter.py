import kaic
from kaic.config import config
from kaic.plotting.base_plotter import BasePlotterMatrix, BasePlotter1D, BasePlotter2D, ScalarDataPlot
from kaic.plotting.helpers import append_axes, style_ticks_whitegrid
from kaic.data.genomic import GenomicRegion
import matplotlib as mpl
from matplotlib.widgets import Slider
from abc import ABCMeta
import numpy as np
import itertools as it
import types
import seaborn as sns
from future.utils import with_metaclass, string_types
import logging
logger = logging.getLogger(__name__)

plt = sns.plt


def prepare_hic_buffer(hic_data, buffering_strategy="relative", buffering_arg=1):
    """
    Prepare :class:`~BufferedMatrix` from hic data.

    :param hic_data: :class:`~kaic.data.genomic.RegionMatrixTable` or
                     :class:`~kaic.data.genomic.RegionMatrix`
    :param buffering_strategy: "all", "fixed" or "relative"
                               "all" buffers the whole matrix
                               "fixed" buffers a fixed area, specified by buffering_arg
                                       around the query area
                               "relative" buffers a multiple of the query area.
                                          With buffering_arg=1 the query area plus
                                          the same amount upstream and downstream
                                          are buffered
    :param buffering_arg: Number specifying how much around the query area is buffered
    """
    if isinstance(hic_data, kaic.data.genomic.RegionMatrixTable):
        return BufferedMatrix(hic_data, buffering_strategy=buffering_strategy,
                                         buffering_arg=buffering_arg)
    elif isinstance(hic_data, kaic.data.genomic.RegionMatrix):
        return BufferedMatrix.from_hic_matrix(hic_data)
    else:
        raise ValueError("Unknown type for hic_data")


class BufferedMatrix(object):
    """
    Buffer contents of any :class:`~kaic.Hic` like objects. Matrix is
    prefetched and stored in memory. Buffer contents can quickly be fetched
    from memory. Different buffering strategies allow buffering of nearby
    regions so that adjacent parts of the matrix can quickly be fetched.
    """
    _STRATEGY_ALL = "all"
    _STRATEGY_FIXED = "fixed"
    _STRATEGY_RELATIVE = "relative"

    def __init__(self, data, buffering_strategy="relative", buffering_arg=1):
        """
        Initialize a buffer for Matrix-like objects that support
        indexing using class:`~GenomicRegion` objects, such as class:`~kaic.Hic`
        or class:`~kaic.RegionMatrix` objects.

        :param data: Data to be buffered
        :param buffering_strategy: "all", "fixed" or "relative"
                                   "all" buffers the whole matrix
                                   "fixed" buffers a fixed area, specified by buffering_arg
                                           around the query area
                                   "relative" buffers a multiple of the query area.
                                              With buffering_arg=1 the query area plus
                                              the same amount upstream and downstream
                                              are buffered
        :param buffering_arg: Number specifying how much around the query area is buffered
        """
        self.data = data
        if buffering_strategy not in self._BUFFERING_STRATEGIES:
            raise ValueError("Only support the buffering strategies {}".format(list(self._BUFFERING_STRATEGIES.keys())))
        self.buffering_strategy = buffering_strategy
        self.buffering_arg = buffering_arg
        self.buffered_region = None
        self.buffered_matrix = None

    @classmethod
    def from_hic_matrix(cls, hic_matrix):
        """
        Wrap a :class:`~HicMatrix` in a :class:`~BufferedMatrix` container.
        Default buffering strategy is set to "all" by default.

        :param hic_matrix: :class:`~HicMatrix`
        :return: :class:`~BufferedMatrix`
        """
        bm = cls(data=None, buffering_strategy="all")
        bm.buffered_region = bm._STRATEGY_ALL
        bm.buffered_matrix = hic_matrix
        return bm

    def is_buffered_region(self, *regions):
        """
        Check if set of :class:`~GenomicRegion`s is already buffered in this matrix.

        :param regions: :class:`~GenomicRegion` object(s)
        :return:
        """
        if (self.buffered_region is None or self.buffered_matrix is None or
                (not self.buffered_region == self._STRATEGY_ALL and not
                 all(rb.contains(rq) for rb, rq in it.izip(self.buffered_region, regions)))):
            return False
        return True

    def get_matrix(self, *regions):
        """
        Retrieve a sub-matrix by the given :class:`~GenomicRegion` object(s).

        Will automatically load data if a non-buffered region is requested.

        :param regions: :class:`~GenomicRegion` object(s)
        :return: :class:`~HicMatrix`
        """
        regions = tuple(reversed([r for r in regions]))
        if not self.is_buffered_region(*regions):
            logger.info("Buffering matrix")
            self._BUFFERING_STRATEGIES[self.buffering_strategy](self, *regions)
        return self.buffered_matrix[tuple(regions)]

    def _buffer_all(self, *regions):
        """
        No buffering, just loads everything in the object into memory.

        Obviously very memory intensive.

        :param regions: :class:`~GenomicRegion` objects
        :return: :class:`~HicMatrix`
        """
        self.buffered_region = self._STRATEGY_ALL
        self.buffered_matrix = self.data[tuple([slice(0, None, None)]*len(regions))]

    def _buffer_relative(self, *regions):
        """
        Load the requested :class:`~GenomicRegion` and buffer an additional fration
        of the matrix given by buffering_arg*len(region)

        :param regions: :class:`~GenomicRegion` objects
        :return: :class:`~HicMatrix`
        """
        self.buffered_region = []
        for rq in regions:
            if rq.start is not None and rq.end is not None:
                rq_size = rq.end - rq.start
                new_start = max(1, rq.start - rq_size*self.buffering_arg)
                new_end = rq.end + rq_size*self.buffering_arg
                self.buffered_region.append(GenomicRegion(start=new_start, end=new_end, chromosome=rq.chromosome))
            else:
                self.buffered_region.append(GenomicRegion(start=None, end=None, chromosome=rq.chromosome))
        self.buffered_matrix = self.data[tuple(self.buffered_region)]

    def _buffer_fixed(self, *regions):
        """
        Load the requested :class:`~GenomicRegion` and buffer an additional
        fixed part of the matrix given by buffering_arg

        :param regions: :class:`~GenomicRegion` objects
        :return: :class:`~HicMatrix`
        """
        self.buffered_region = []
        for rq in regions:
            if rq.start is not None and rq.end is not None:
                new_start = max(1, rq.start - self.buffering_arg)
                new_end = rq.end + self.buffering_arg
                self.buffered_region.append(GenomicRegion(start=new_start, end=new_end, chromosome=rq.chromosome))
            else:
                self.buffered_region.append(GenomicRegion(start=None, end=None, chromosome=rq.chromosome))
        self.buffered_matrix = self.data[tuple(self.buffered_region)]

    @property
    def buffered_min(self):
        """
        Find the smallest non-zero buffered matrix value.

        :return: float or None if nothing is buffered
        """
        return float(np.nanmin(self.buffered_matrix[np.ma.nonzero(self.buffered_matrix)]))\
            if self.buffered_matrix is not None else None

    @property
    def buffered_max(self):
        """
        Find the largest buffered matrix value
        :return: float or None if nothing is buffered
        """
        return float(np.nanmax(self.buffered_matrix)) if self.buffered_matrix is not None else None

    _BUFFERING_STRATEGIES = {_STRATEGY_ALL: _buffer_all,
                             _STRATEGY_RELATIVE: _buffer_relative,
                             _STRATEGY_FIXED: _buffer_fixed}


class BufferedCombinedMatrix(BufferedMatrix):
    """
    A buffered square matrix where values above and below the diagonal
    come from different matrices.
    """
    def __init__(self, top_matrix, bottom_matrix, scale_matrices=True, buffering_strategy="relative", buffering_arg=1):
        super(BufferedCombinedMatrix, self).__init__(None, buffering_strategy, buffering_arg)

        scaling_factor = 1
        if scale_matrices:
            scaling_factor = top_matrix.scaling_factor(bottom_matrix)

        class CombinedData(object):
            def __init__(self, hic_top, hic_bottom, scaling_factor=1):
                self.hic_top = hic_top
                self.hic_bottom = hic_bottom
                self.scaling_factor = scaling_factor

            def __getitem__(self, item):
                return top_matrix.get_combined_matrix(self.hic_bottom, key=item, scaling_factor=self.scaling_factor)

        self.data = CombinedData(top_matrix, bottom_matrix, scaling_factor)


class BasePlotterHic(with_metaclass(ABCMeta, BasePlotterMatrix)):
    """
    Base class for plotting Hi-C data.

    Makes use of matrix buffering by :class:`~BufferedMatrix` internally.
    """

    def __init__(self, hic_data, colormap=config.colormap_hic, norm="log",
                 vmin=None, vmax=None, show_colorbar=True, adjust_range=True,
                 buffering_strategy="relative", buffering_arg=1, blend_zero=True,
                 unmappable_color=".9", illegal_color=None, colorbar_symmetry=None):
        BasePlotterMatrix.__init__(self, colormap=colormap, norm=norm,
                                   vmin=vmin, vmax=vmax, show_colorbar=show_colorbar,
                                   blend_zero=blend_zero, unmappable_color=unmappable_color,
                                   illegal_color=illegal_color, colorbar_symmetry=colorbar_symmetry)
        self.hic_data = hic_data
        self.hic_buffer = prepare_hic_buffer(hic_data, buffering_strategy=buffering_strategy,
                                             buffering_arg=buffering_arg)
        self.slider = None
        self.adjust_range = adjust_range
        self.vmax_slider = None


class HicPlot2D(BasePlotter2D, BasePlotterHic):
    def __init__(self, hic_data, title='', colormap=config.colormap_hic, norm="log",
                 vmin=None, vmax=None, show_colorbar=True, colorbar_symmetry=None,
                 adjust_range=True, buffering_strategy="relative", buffering_arg=1,
                 blend_zero=True, unmappable_color=".9",
                 aspect=1., axes_style="ticks"):
        """
        Initialize a 2D Hi-C heatmap plot.

        :param hic_data: class:`~kaic.Hic` or class:`~kaic.RegionMatrix`
        :param title: Title drawn on top of the figure panel
        :param colormap: Can be the name of a colormap or a Matplotlib colormap instance
        :param norm: Can be "lin", "log" or any Matplotlib Normalization instance
        :param vmin: Clip interactions below this value
        :param vmax: Clip interactions above this value
        :param show_colorbar: Draw a colorbar
        :param adjust_range: Draw a slider to adjust vmin/vmax interactively
        :param buffering_strategy: A valid buffering strategy for class:`~BufferedMatrix`
        :param buffering_arg: Adjust range of buffering for class:`~BufferedMatrix`
        :param blend_zero: If True then zero count bins will be drawn using the minimum
                           value in the colormap, otherwise transparent
        :param unmappable_color: Draw unmappable bins using this color. Defaults to
                                 light gray (".9")
        :param aspect: Default aspect ratio of the plot. Can be overriden by setting
                       the height_ratios in class:`~GenomicFigure`
        """
        BasePlotter2D.__init__(self, title=title, aspect=aspect, axes_style=axes_style)
        BasePlotterHic.__init__(self, hic_data=hic_data, colormap=colormap,
                                norm=norm, vmin=vmin, vmax=vmax, show_colorbar=show_colorbar,
                                adjust_range=adjust_range, buffering_strategy=buffering_strategy,
                                buffering_arg=buffering_arg, blend_zero=blend_zero,
                                unmappable_color=unmappable_color, colorbar_symmetry=colorbar_symmetry)
        self.vmax_slider = None
        self.current_matrix = None

    def _plot(self, region=None, ax=None, *args, **kwargs):
        self.current_matrix = self.hic_buffer.get_matrix(*region)
        self.im = self.ax.imshow(self.get_color_matrix(self.current_matrix), interpolation='none',
                                 cmap=self.colormap, norm=self.norm, origin="upper",
                                 extent=[self.current_matrix.col_regions[0].start, self.current_matrix.col_regions[-1].end,
                                         self.current_matrix.row_regions[-1].end, self.current_matrix.row_regions[0].start])
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['top'].set_visible(False)
        self.ax.xaxis.set_ticks_position('bottom')
        self.ax.yaxis.set_ticks_position('left')

        if self.show_colorbar:
            self.add_colorbar()
        if self.adjust_range:
            self.add_adj_slider()

    def add_adj_slider(self, ax=None):
        if ax is None:
            ax = append_axes(self.ax, 'top', 0.2, 0.25)

        vmin = self.hic_buffer.buffered_min
        vmax = self.hic_buffer.buffered_max
        self.vmax_slider = Slider(ax, 'vmax', vmin,
                                  vmax, valinit=self.vmax,
                                  facecolor='#dddddd', edgecolor='none')

        self.vmax_slider.on_changed(self._slider_refresh)

    def _slider_refresh(self, val):
        # new_vmin = self.vmin_slider.val
        new_vmax = self.vmax_slider.val
        if self.colorbar_symmetry is not None:
            diff = abs(self.colorbar_symmetry - new_vmax)
            self.im.set_clim(vmin=self.colorbar_symmetry - diff, vmax=self.colorbar_symmetry + diff)
        else:
            self.im.set_clim(vmin=self.hic_buffer.buffered_min, vmax=new_vmax)
        # Hack to force redraw of image data
        self.im.set_data(self.current_matrix)

        if self.colorbar is not None:
            self.update_colorbar(vmax=new_vmax)

    def _refresh(self, region=None, ax=None, *args, **kwargs):
        self.current_matrix = self.hic_buffer.get_matrix(*region)
        self.im.set_data(self.get_color_matrix(self.current_matrix))
        self.im.set_extent([self.current_matrix.col_regions[0].start, self.current_matrix.col_regions[-1].end,
                            self.current_matrix.row_regions[-1].end, self.current_matrix.row_regions[0].start])


class HicSideBySidePlot2D(object):
    def __init__(self, hic1, hic2, colormap=config.colormap_hic, norm="log",
                 vmin=None, vmax=None, aspect=1., axes_style="ticks"):
        self.hic_plotter1 = HicPlot2D(hic1, colormap=colormap, norm=norm,
                                      vmin=vmin, vmax=vmax, aspect=aspect, axes_style=axes_style)
        self.hic_plotter2 = HicPlot2D(hic2, colormap=colormap, norm=norm,
                                      vmin=vmin, vmax=vmax, aspect=aspect, axes_style=axes_style)

    def plot(self, region):
        fig = plt.figure()
        ax1 = plt.subplot(121)
        ax2 = plt.subplot(122, sharex=ax1, sharey=ax1)

        self.hic_plotter1.plot(x_region=region, y_region=region, ax=ax1)
        self.hic_plotter2.plot(x_region=region, y_region=region, ax=ax2)

        return fig, ax1, ax2


class HicComparisonPlot2D(HicPlot2D):
    def __init__(self, hic_top, hic_bottom, colormap=config.colormap_hic, norm='log',
                 vmin=None, vmax=None, scale_matrices=True, show_colorbar=True,
                 buffering_strategy="relative", buffering_arg=1, aspect=1.,
                 axes_style="ticks"):
        super(HicComparisonPlot2D, self).__init__(hic_top, colormap=colormap, norm=norm,
                                                  vmin=vmin, vmax=vmax,
                                                  show_colorbar=show_colorbar, aspect=aspect,
                                                  axes_style=axes_style)
        self.hic_top = hic_top
        self.hic_bottom = hic_bottom
        self.hic_buffer = BufferedCombinedMatrix(hic_bottom, hic_top, scale_matrices,
                                                 buffering_strategy, buffering_arg)


class HicSlicePlot(ScalarDataPlot):
    def __init__(self, hic_data, slice_region, names=None, style="step", title='',
                 aspect=.3, axes_style=style_ticks_whitegrid, ylim=None, yscale="linear",
                 buffering_strategy="relative", buffering_arg=1):
        """
        Initialize a plot which draws Hi-C data as virtual 4C-plot. All interactions that
        involve the slice region are shown.

        :param hic_data: class:`~kaic.Hic` or class:`~kaic.RegionMatrix`. Can be list of
                         multiple Hi-C datasets.
        :param slice_region: String ("2L:1000000-1500000") or :class:`~GenomicRegion`.
                             All interactions involving this region are shown.
        :param names: If multiple Hi-C datasets are provided, can pass a list of names.
                      Are used as names in the legend of the plot.
        :param style: 'step' Draw values in a step-wise manner for each bin
                      'mid' Draw values connecting mid-points of bins
        :param aspect: Default aspect ratio of the plot. Can be overriden by setting
                       the height_ratios in class:`~GenomicFigure`
        :param title: Title drawn on top of the figure panel
        :param ylim: Tuple to set y-axis limits
        :param y_scale: Set scale of the y-axis, is passed to Matplotlib set_yscale, so any
                        valid argument ("linear", "log", etc.) works
        :param buffering_strategy: A valid buffering strategy for class:`~BufferedMatrix`
        :param buffering_arg: Adjust range of buffering for class:`~BufferedMatrix`
        """
        ScalarDataPlot.__init__(self, style=style, title=title, aspect=aspect,
                                axes_style=axes_style)
        if not isinstance(hic_data, (list, tuple)):
            hic_data = [hic_data]
        self.hic_buffers = []
        for h in hic_data:
            hb = prepare_hic_buffer(h,
                                    buffering_strategy=buffering_strategy,
                                    buffering_arg=buffering_arg)
            self.hic_buffers.append(hb)
        self.names = names
        if isinstance(slice_region, string_types):
            slice_region = GenomicRegion.from_string(slice_region)
        self.slice_region = slice_region
        self.yscale = yscale
        self.ylim = ylim
        self.x = None
        self.y = None

    def _plot(self, region=None, ax=None, *args, **kwargs):
        for i, b in enumerate(self.hic_buffers):
            hm = b.get_matrix(self.slice_region, region).T
            hm = np.mean(b.get_matrix(self.slice_region, region).T, axis=0)
            bin_coords = np.r_[[x.start for x in hm.row_regions], hm.row_regions[-1].end]
            bin_coords = (bin_coords[1:] + bin_coords[:-1])/2
            self.ax.plot(bin_coords, hm, label=self.names[i] if self.names else "")
        if self.names:
            self.add_legend()
        self.remove_colorbar_ax()
        sns.despine(ax=self.ax, top=True, right=True)
        self.ax.set_yscale(self.yscale)
        if self.ylim:
            self.ax.set_ylim(self.ylim)

    def _refresh(self, region=None, ax=None, *args, **kwargs):
        pass


class HicPlot(BasePlotter1D, BasePlotterHic):
    def __init__(self, hic_data, title='', colormap=config.colormap_hic, max_dist=None, norm="log",
                 vmin=None, vmax=None, show_colorbar=True, adjust_range=False, colorbar_symmetry=None,
                 buffering_strategy="relative", buffering_arg=1, blend_zero=True,
                 unmappable_color=".9", illegal_color=None, aspect=.5,
                 axes_style="ticks"):
        """
        Initialize a triangle Hi-C heatmap plot.

        :param hic_data: class:`~kaic.Hic` or class:`~kaic.RegionMatrix`
        :param title: Title drawn on top of the figure panel
        :param colormap: Can be the name of a colormap or a Matplotlib colormap instance
        :param norm: Can be "lin", "log" or any Matplotlib Normalization instance
        :param max_dist: Only draw interactions up to this distance
        :param vmin: Clip interactions below this value
        :param vmax: Clip interactions above this value
        :param show_colorbar: Draw a colorbar
        :param adjust_range: Draw a slider to adjust vmin/vmax interactively
        :param buffering_strategy: A valid buffering strategy for class:`~BufferedMatrix`
        :param buffering_arg: Adjust range of buffering for class:`~BufferedMatrix`
        :param blend_zero: If True then zero count bins will be drawn using the minimum
                           value in the colormap, otherwise transparent
        :param unmappable_color: Draw unmappable bins using this color. Defaults to
                                 light gray (".9")
        :param illegal_color: Draw non-finite (NaN, +inf, -inf) bins using this color. Defaults to
                                 None (no special color).
        :param aspect: Default aspect ratio of the plot. Can be overriden by setting
                       the height_ratios in class:`~GenomicFigure`
        """

        BasePlotterHic.__init__(self, hic_data, colormap=colormap, vmin=vmin, vmax=vmax,
                                show_colorbar=show_colorbar, adjust_range=adjust_range,
                                buffering_strategy=buffering_strategy, buffering_arg=buffering_arg,
                                norm=norm, blend_zero=blend_zero, unmappable_color=unmappable_color,
                                illegal_color=illegal_color, colorbar_symmetry=colorbar_symmetry)
        BasePlotter1D.__init__(self, title=title, aspect=aspect, axes_style=axes_style)

        self.max_dist = max_dist
        self.hm = None

    def _plot(self, region=None, *args, **kwargs):
        logger.debug("Generating matrix from hic object")
        if region is None:
            raise ValueError("Cannot plot triangle plot for whole genome.")
        if region.start is None:
            region.start = 1
        if region.end is None:
            region.end = self.hic_data.chromosome_lens[region.chromosome]
        # Have to copy unfortunately, otherwise modifying matrix in buffer
        x_, y_, hm = self._mesh_data(region)
        self.hm = hm

        self.collection = self.ax.pcolormesh(x_, y_, hm, cmap=self.colormap, norm=self.norm, rasterized=True)
        self.collection._A = None
        self._update_mesh_colors()

        # set limits and aspect ratio
        # self.ax.set_aspect(aspect="equal")
        self.ax.set_ylim(0, self.max_dist/2 if self.max_dist else (region.end-region.start)/2)
        # remove outline everywhere except at bottom
        sns.despine(ax=self.ax, top=True, right=True, left=True)
        self.ax.set_yticks([])
        # hide background patch
        self.ax.patch.set_visible(False)
        if self.show_colorbar:
            self.add_colorbar(ax=None)
        if self.adjust_range:
            self.add_adj_slider()

        def drag_pan(self, button, key, x, y):
            mpl.axes.Axes.drag_pan(self, button, 'x', x, y)  # pretend key=='x'

        self.ax.drag_pan = types.MethodType(drag_pan, self.ax)

    def _mesh_data(self, region):
        hm = self.hic_buffer.get_matrix(region, region)
        hm_copy = kaic.data.genomic.RegionMatrix(np.copy(hm), col_regions=hm.col_regions,
                                                 row_regions=hm.row_regions)
        # update coordinates
        bin_coords = np.r_[[x.start for x in hm_copy.row_regions], hm_copy.row_regions[-1].end]
        # Make sure the matrix is not protruding over the end of the requested plotting region
        if bin_coords[0] < region.start <= bin_coords[1]:
            bin_coords[0] = region.start
        if bin_coords[-1] > region.end >= bin_coords[-2]:
            bin_coords[-1] = region.end
        bin_coords = np.true_divide(bin_coords, np.sqrt(2))
        x, y = np.meshgrid(bin_coords, bin_coords)
        # rotatate coordinate matrix 45 degrees
        sin45 = np.sin(np.radians(45))
        x_, y_ = x * sin45 + y * sin45, x * sin45 - y * sin45

        return x_, y_, hm_copy

    def _update_mesh_colors(self):
        # pcolormesh doesn't support plotting RGB arrays directly like imshow, have to workaround
        # See https://github.com/matplotlib/matplotlib/issues/4277
        # http://stackoverflow.com/questions/29232439/plotting-an-irregularly-spaced-rgb-image-in-python/29232668?noredirect=1#comment46710586_29232668
        color_matrix = self.get_color_matrix(self.hm)
        color_tuple = color_matrix.transpose((1, 0, 2)).reshape(
            (color_matrix.shape[0] * color_matrix.shape[1], color_matrix.shape[2]))
        self.collection.set_color(color_tuple)

    def _refresh(self, region=None, *args, **kwargs):
        x_, y_, hm = self._mesh_data(region)
        self.hm = hm

        self.collection._coordinates[:, :, 0] = x_
        # update matrix data
        self.collection.set_array(self.hm.ravel())
        self._update_mesh_colors()

    def add_adj_slider(self, ax=None):
        if ax is None:
            ax = append_axes(self.ax, 'top', 1, 0.05)

        self.vmax_slider = Slider(ax, 'vmax', self.hic_buffer.buffered_min,
                                  self.hic_buffer.buffered_max, valinit=self.vmax,
                                  facecolor='#dddddd', edgecolor='none')

        self.vmax_slider.on_changed(self._slider_refresh)

    def _slider_refresh(self, val):
        # new_vmin = self.vmin_slider.val
        new_vmax = self.vmax_slider.val
        if self.colorbar_symmetry is not None:
            diff = abs(self.colorbar_symmetry-new_vmax)
            self._update_norm(vmin=self.colorbar_symmetry-diff, vmax=self.colorbar_symmetry+diff)
        else:
            self._update_norm(vmax=new_vmax)
        self._update_mesh_colors()
        if self.colorbar is not None:
            self.update_colorbar(vmax=new_vmax)
