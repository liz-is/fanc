from kaic.plotting.plot_genomic_data import hic_correlation_plot, hic_matrix_plot, hic_contact_plot_linear, \
                                            hic_matrix_diff_plot, hic_matrix_ratio_plot
from kaic.plotting.plot_statistics import plot_mask_statistics
from kaic.plotting.colormaps import *
import seaborn as sns
import logging

logging.basicConfig(level=logging.INFO)
sns.plt.register_cmap(name='viridis', cmap=viridis)
sns.plt.register_cmap(name='plasma', cmap=plasma)
sns.plt.register_cmap(name='inferno', cmap=inferno)
sns.plt.register_cmap(name='magma', cmap=magma)
