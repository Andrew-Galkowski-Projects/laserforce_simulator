import cv2
import numpy as np
from PIL import Image

# Cells that are valid player positions for LOS computation.
# Low wall (4) and windowed wall (5) block movement and are not LOS origins, but are transparent to LOS.
# Legacy values 2/3 (red/blue zone) kept for backward compatibility with old maps.
_LOS_PASSABLE = {1, 2, 3}


# TODO: eventually want to process 3d maps in order to use some maps that are not usable without it
# ── Quadtree Node for Spatial Acceleration ──────────────────────────────────
class QuadtreeNode:
    """Quadtree node for spatial partitioning of passable cells."""

    def __init__(self, r_min, r_max, c_min, c_max, max_depth=6, depth=0):
        self.r_min, self.r_max = r_min, r_max
        self.c_min, self.c_max = c_min, c_max
        self.depth = depth
        self.max_depth = max_depth
        self.passable_cells = []
        self.children = None

    def add_cell(self, r, c):
        """Add a passable cell to this node."""
        if self.children is None and self.depth < self.max_depth:
            self._try_subdivide()

        if self.children is None:
            self.passable_cells.append((r, c))
        else:
            for child in self.children:
                if child.r_min <= r <= child.r_max and child.c_min <= c <= child.c_max:
                    child.add_cell(r, c)
                    break

    def _try_subdivide(self):
        """Split into 4 children if we have enough cells."""
        if len(self.passable_cells) <= 4:
            return
        r_mid = (self.r_min + self.r_max) // 2
        c_mid = (self.c_min + self.c_max) // 2
        self.children = [
            QuadtreeNode(
                self.r_min, r_mid, self.c_min, c_mid, self.max_depth, self.depth + 1
            ),
            QuadtreeNode(
                self.r_min, r_mid, c_mid + 1, self.c_max, self.max_depth, self.depth + 1
            ),
            QuadtreeNode(
                r_mid + 1, self.r_max, self.c_min, c_mid, self.max_depth, self.depth + 1
            ),
            QuadtreeNode(
                r_mid + 1,
                self.r_max,
                c_mid + 1,
                self.c_max,
                self.max_depth,
                self.depth + 1,
            ),
        ]
        # Redistribute cells
        cells = self.passable_cells
        self.passable_cells = []
        for r, c in cells:
            for child in self.children:
                if child.r_min <= r <= child.r_max and child.c_min <= c <= child.c_max:
                    child.passable_cells.append((r, c))
                    break

    def nearby_cells(self, r, c, radius):
        """Get all passable cells within radius of (r, c)."""
        if not self._bounds_overlap(r, c, radius):
            return []
        nearby = []
        if self.children is None:
            nearby.extend(self.passable_cells)
        else:
            for child in self.children:
                nearby.extend(child.nearby_cells(r, c, radius))
        return nearby

    def _bounds_overlap(self, r, c, radius):
        """Check if this node's bounding box overlaps the search radius."""
        r_overlaps = not (r + radius < self.r_min or r - radius > self.r_max)
        c_overlaps = not (c + radius < self.c_min or c - radius > self.c_max)
        return r_overlaps and c_overlaps


def detect_zones(image_path, cell_size):
    # Use the processed B&W image for wall detection; read dimensions from it.
    processed_bw_pil = create_processed_image(image_path)
    processed_bw = np.array(processed_bw_pil)
    img_width, img_height = processed_bw_pil.size

    cols = img_width // cell_size
    rows = img_height // cell_size

    zones = []
    for r in range(rows):
        row = []
        for c in range(cols):
            x = c * cell_size
            y = r * cell_size

            # Check the processed B&W image for wall detection
            cell_bw = processed_bw_pil.crop((x, y, x + cell_size, y + cell_size))
            bw_pixels = list(cell_bw.getdata())
            # In processed image: 0=wall (black), 255=background (white)
            # Count dark pixels (walls)
            dark_pixel_count = sum(1 for p in bw_pixels if p < 128)
            wall_ratio = dark_pixel_count / len(bw_pixels)

            # Determine zone type: wall (0) or floor (1).
            # Red/blue zone coloring (2/3) was a legacy 3-zone artifact — no longer produced.
            if wall_ratio > 0.01:  # Cell is >1% walls
                zone_type = 0  # high wall
            else:
                zone_type = 1  # floor

            row.append(zone_type)
        zones.append(row)

    # Compute edge blocking for sub-cell precision
    blocked_edges_dict, blocked_edges_grid = _compute_blocked_edges(
        processed_bw, rows, cols, cell_size
    )

    return {
        "rows": rows,
        "cols": cols,
        "cell_size": cell_size,
        "img_width": img_width,
        "img_height": img_height,
        "zones": zones,
        "blocked_edges": blocked_edges_dict,
        "blocked_edges_grid": blocked_edges_grid,
    }


def create_processed_image(image_path):
    """Return a B&W PIL Image with text labels removed.

    Converts to grayscale, thresholds to find all dark features (walls + text),
    then uses connected-component analysis to keep large wall segments and
    discard small text blobs.
    """
    img_pil = Image.open(image_path).convert("RGB")
    img_array = np.array(img_pil)

    gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)

    # Threshold: anything darker than near-white becomes black (walls + text).
    # 210 catches most colored text (yellow labels read ~200 in grayscale).
    _, binary = cv2.threshold(gray, 210, 255, cv2.THRESH_BINARY)
    # binary: 255 = background, 0 = features

    # Invert so features are white (required by connectedComponentsWithStats)
    features = cv2.bitwise_not(binary)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        features, connectivity=8
    )

    # Rebuild image: keep only components that are large or elongated (walls).
    # Small square-ish blobs are text characters.
    clean = np.full_like(binary, 255)
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        if area >= 600 or max(w, h) >= 80:
            clean[labels == i] = 0

    return Image.fromarray(clean)


def _compute_blocked_edges(processed_bw, rows, cols, cell_size):
    """Compute which edges between cells are blocked by walls.

    Returns: (dict, 2d_array)
      - dict: "r,c,direction" -> True (backward compat)
      - 2d_array: blocked_edges_grid[r][c] = {"e": bool, "s": bool}

    Directions: 'e' (east)=right, 's' (south)=down
    """
    blocked_dict = {}
    blocked_grid = [
        [{"e": False, "s": False} for _ in range(cols)] for _ in range(rows)
    ]

    for r in range(rows):
        for c in range(cols):
            # Check east edge (edge to cell at r, c+1)
            if c < cols - 1:
                x1 = (c + 1) * cell_size
                y_start = r * cell_size
                y_end = min((r + 1) * cell_size, processed_bw.shape[0])
                if x1 < processed_bw.shape[1]:
                    edge_pixels = processed_bw[y_start:y_end, x1]
                    wall_ratio = (
                        np.sum(edge_pixels < 128) / len(edge_pixels)
                        if len(edge_pixels) > 0
                        else 0
                    )
                    if wall_ratio > 0.3:
                        blocked_dict[f"{r},{c},e"] = True
                        blocked_grid[r][c]["e"] = True

            # Check south edge (edge to cell at r+1, c)
            if r < rows - 1:
                y1 = (r + 1) * cell_size
                x_start = c * cell_size
                x_end = min((c + 1) * cell_size, processed_bw.shape[1])
                if y1 < processed_bw.shape[0]:
                    edge_pixels = processed_bw[y1, x_start:x_end]
                    wall_ratio = (
                        np.sum(edge_pixels < 128) / len(edge_pixels)
                        if len(edge_pixels) > 0
                        else 0
                    )
                    if wall_ratio > 0.3:
                        blocked_dict[f"{r},{c},s"] = True
                        blocked_grid[r][c]["s"] = True

    return blocked_dict, blocked_grid


def _has_los(zone_data, r1, c1, r2, c2, blocked_edges_grid=None):
    """Bresenham's line — True if no wall cell lies on the path.

    Optimizations:
    - Early termination: adjacent cells always visible (unless blocked edge)
    - Efficient edge lookup: uses 2D array instead of dict string keys
    - Caches direction values to avoid repeated comparisons
    """
    # Early termination: adjacent cells
    if abs(r1 - r2) <= 1 and abs(c1 - c2) <= 1:
        if r1 == r2 and c1 == c2:
            return False
        # Check edge blocking only if different cell
        if blocked_edges_grid and r1 == r2:  # Same row, different column
            col_src, col_dst = (c1, c2) if c1 < c2 else (c2, c1)
            return not blocked_edges_grid[r1][col_src].get("e", False)
        elif blocked_edges_grid and c1 == c2:  # Same column, different row
            row_src, row_dst = (r1, r2) if r1 < r2 else (r2, r1)
            return not blocked_edges_grid[row_src][c1].get("s", False)
        return True

    # Bresenham's line algorithm
    x0, y0 = c1, r1
    x1, y1 = c2, r2
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    cx, cy = x0, y0

    while True:
        e2 = 2 * err
        moved_x, moved_y = False, False

        if e2 > -dy:
            err -= dy
            cx += sx
            moved_x = True
        if e2 < dx:
            err += dx
            cy += sy
            moved_y = True

        if cx == x1 and cy == y1:
            return True

        # High wall (0) blocks LOS.
        # Low wall (4) and windowed wall (5) are transparent — sight passes through but movement does not.
        if zone_data[cy][cx] == 0:
            return False

        # Check edge blocking (optimized with 2D grid lookup)
        if blocked_edges_grid and (moved_x or moved_y):
            prev_cx, prev_cy = cx - sx if moved_x else cx, cy - sy if moved_y else cy

            # Check edge from previous to current
            if moved_x and prev_cy in range(len(blocked_edges_grid)):
                if prev_cx in range(len(blocked_edges_grid[0])):
                    if sx > 0 and blocked_edges_grid[prev_cy][prev_cx].get("e", False):
                        return False
                    elif (
                        sx < 0
                        and prev_cx > 0
                        and blocked_edges_grid[prev_cy][prev_cx - 1].get("e", False)
                    ):
                        return False

            if moved_y and cy in range(len(blocked_edges_grid)):
                if prev_cx in range(len(blocked_edges_grid[0])):
                    if sy > 0 and blocked_edges_grid[prev_cy][prev_cx].get("s", False):
                        return False
                    elif (
                        sy < 0
                        and prev_cy > 0
                        and blocked_edges_grid[prev_cy - 1][prev_cx].get("s", False)
                    ):
                        return False


def compute_sight_lines(zone_data, blocked_edges_grid=None, use_quadtree=True):
    """Compute bidirectional adjacency dict for all non-wall cell pairs.

    Args:
        zone_data: Either 2D list or dict with 'zones' and 'blocked_edges_grid'
        blocked_edges_grid: Optional 2D array of blocked edges (for optimization)
        use_quadtree: Use spatial acceleration (recommended for large maps)

    Returns: {"r,c": ["r,c", ...]} sight lines

    Optimizations:
    - Quadtree spatial partitioning: ~50x speedup for 30px+ zones
    - Only tests cells within visibility radius of each cell
    - Early termination in raycasting
    """
    # Handle both old (2D list) and new (dict) formats
    if isinstance(zone_data, dict):
        blocked_edges_grid = zone_data.get(
            "blocked_edges_grid", zone_data.get("blocked_edges", {})
        )
        zone_grid = zone_data["zones"]
    else:
        zone_grid = zone_data
        if blocked_edges_grid is None:
            blocked_edges_grid = {}

    rows = len(zone_grid)
    cols = len(zone_grid[0]) if rows else 0
    passable = [
        (r, c)
        for r in range(rows)
        for c in range(cols)
        if zone_grid[r][c] in _LOS_PASSABLE
    ]

    if not passable:
        return {}

    sight = {}

    # Option 1: Quadtree-accelerated (recommended)
    if use_quadtree and len(passable) > 50:
        quadtree = QuadtreeNode(0, rows - 1, 0, cols - 1, max_depth=6)
        for r, c in passable:
            quadtree.add_cell(r, c)

        # Visibility radius: conservative estimate (diagonal / 4)
        vis_radius = max(rows, cols) // 4

        for r1, c1 in passable:
            nearby = quadtree.nearby_cells(r1, c1, vis_radius)
            for r2, c2 in nearby:
                if (r1, c1) < (r2, c2):  # Avoid duplicates
                    if _has_los(zone_grid, r1, c1, r2, c2, blocked_edges_grid):
                        sight.setdefault(f"{r1},{c1}", []).append(f"{r2},{c2}")
                        sight.setdefault(f"{r2},{c2}", []).append(f"{r1},{c1}")

    # Option 2: Brute force (for small maps or fallback)
    else:
        n = len(passable)
        for i in range(n):
            r1, c1 = passable[i]
            for j in range(i + 1, n):
                r2, c2 = passable[j]
                if _has_los(zone_grid, r1, c1, r2, c2, blocked_edges_grid):
                    sight.setdefault(f"{r1},{c1}", []).append(f"{r2},{c2}")
                    sight.setdefault(f"{r2},{c2}", []).append(f"{r1},{c1}")

    return sight


def compute_single_cell_visibility(r1, c1, zone_data, blocked_edges_grid=None):
    """Compute sight lines from a single cell (lazy mode for editor).

    Much faster than all-pairs computation: O(n) instead of O(n²).
    Used when user clicks a cell in the editor.

    Args:
        r1, c1: Cell to compute visibility from
        zone_data: Dict with 'zones' and optionally 'blocked_edges_grid'
        blocked_edges_grid: Optional 2D array of blocked edges

    Returns: List of visible cell keys "r,c"
    """
    if isinstance(zone_data, dict):
        blocked_edges_grid = zone_data.get(
            "blocked_edges_grid", zone_data.get("blocked_edges", {})
        )
        zone_grid = zone_data["zones"]
    else:
        zone_grid = zone_data
        if blocked_edges_grid is None:
            blocked_edges_grid = {}

    rows = len(zone_grid)
    cols = len(zone_grid[0]) if rows else 0

    if not (0 <= r1 < rows and 0 <= c1 < cols and zone_grid[r1][c1] in _LOS_PASSABLE):
        return []

    visible = []
    passable = [
        (r, c)
        for r in range(rows)
        for c in range(cols)
        if zone_grid[r][c] in _LOS_PASSABLE
    ]

    for r2, c2 in passable:
        if (r1, c1) != (r2, c2) and _has_los(
            zone_grid, r1, c1, r2, c2, blocked_edges_grid
        ):
            visible.append(f"{r2},{c2}")

    return visible


def compute_high_los_ranking(sight_data: dict) -> list[list[int]]:
    """Return all cells sorted by LOS count descending (highest visibility first).

    sight_data is {"r,c": ["r,c", ...]} as stored in SightLineConfig.
    Returns [[row, col], ...] — cells with more visible neighbours come first.
    """
    counts = []
    for key, visible in sight_data.items():
        parts = key.split(",")
        counts.append((int(parts[0]), int(parts[1]), len(visible)))
    counts.sort(key=lambda x: x[2], reverse=True)
    return [[r, c] for r, c, _ in counts]


def compute_heavy_strong_spots(sight_data: dict) -> list[list[int]]:
    """Return the top 25% of cells by LOS count as Heavy strong-spot candidates.

    Auto-seeds HeavyStrongSpotsConfig when sight lines are first computed.
    Returns [[row, col], ...] in LOS-count order (highest first).
    """
    ranked = compute_high_los_ranking(sight_data)
    top_n = max(1, len(ranked) // 4)
    return ranked[:top_n]
