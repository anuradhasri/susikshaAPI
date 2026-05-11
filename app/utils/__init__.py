# Utils module
from app.utils.logger import setup_logging
from app.utils.query_utils import (
    filter_by_region,
    calculate_similarity,
    detect_duplicate_patients,
    check_exact_duplicates,
    soft_delete
)
