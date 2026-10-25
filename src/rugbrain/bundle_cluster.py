"""bundle_cluster.py - import surface preserved; implementation split into submodules."""
from .bundle_cluster_p1 import logger, embed_bundle_profile  # noqa: F401
from .bundle_cluster_p2 import embed_cluster_profile  # noqa: F401
from .bundle_cluster_p3 import CLUSTER_LABEL_TEMPLATES, auto_label_cluster, find_similar_clusters  # noqa: F401
from .bundle_cluster_p4 import find_similar_bundles, search_clusters_by_description, index_bundle_detection, index_cluster_detection, backfill_label_templates  # noqa: F401
