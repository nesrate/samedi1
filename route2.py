#from flask import Blueprint
#from flask import Flask, request, render_template, send_file, jsonify
#import os
#import ezdxf
#import re
#from shapely.geometry import LineString, Point, Polygon, MultiLineString, MultiPolygon
#from shapely.ops import polygonize, unary_union, snap, linemerge, nearest_points
#import math
#import numpy as np
#from PIL import Image
#import io
#import fitz
#import easyocr
from flask import Blueprint, request, render_template, send_file, jsonify
import os
import ezdxf
import re
from shapely.geometry import LineString, Point, Polygon, MultiLineString, MultiPolygon
from shapely.ops import polygonize, unary_union, snap, linemerge, nearest_points
import math
import numpy as np
from PIL import Image
import io
import fitz
import easyocr

#app = Flask(__name__)
bp2 = Blueprint('route2', __name__)

#@bp2.route("/")
#def index():
#    return render_template("2d3d.html")
@bp2.route("/2d3d")
def index():
    return render_template("2d3d.html")


# =========================
# دوال تصحيح OCR
# =========================

UPLOAD_FOLDER = "uploads"  
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


current_file_path = None

def clean_geometry_coords(coords, min_distance=0.05):
    if len(coords) < 3:
        return coords
    
    cleaned = [coords[0]]
    for i in range(1, len(coords)):
        p1 = Point(cleaned[-1])
        p2 = Point(coords[i])
        
        if p1.distance(p2) > min_distance:
            cleaned.append(coords[i])
    
    if len(cleaned) > 2:
        p_first = Point(cleaned[0])
        p_last = Point(cleaned[-1])
        if p_first.distance(p_last) < min_distance:
            cleaned[-1] = cleaned[0]
        else:
            cleaned.append(cleaned[0])
    
    return cleaned



# ============================================================
# NOUVELLE FONCTION (sans modifier les existantes)
# ============================================================


def process_and_merge_polylines(gap_fillers_lines, target_polylines_coords, distance_threshold=1.22):
    print(f"\n=== معالجة ودمج Polylines (المسافة العتبة: {distance_threshold}) ===")
    
    if not gap_fillers_lines:
        print("لا توجد خطوط GAPS_FILL_2D للمعالجة")
        return target_polylines_coords
    
    target_polygons = []
    for coords in target_polylines_coords:
        try:
            if len(coords) >= 3:
                polygon = Polygon(coords)
                if polygon.is_valid and not polygon.is_empty:
                    target_polygons.append(polygon)
        except:
            continue
    
    gap_polygons = []
    for line in gap_fillers_lines:
        try:
            coords = list(line.coords)
            if len(coords) >= 2:
                buffered = line.buffer(0.05)
                if isinstance(buffered, Polygon):
                    gap_polygons.append(buffered)
                elif isinstance(buffered, MultiPolygon):
                    for poly in buffered.geoms:
                        gap_polygons.append(poly)
        except:
            continue
    
    print(f"عدد Polygons من GAPS_FILL_2D: {len(gap_polygons)}")
    
    all_polygons = target_polygons + gap_polygons
    merged = unary_union(all_polygons)
    merged = merged.buffer(0)
    
    result_coords_list = []
    
    if isinstance(merged, Polygon):
        coords = list(merged.exterior.coords)
        cleaned_coords = clean_geometry_coords(coords)
        result_coords_list.append(cleaned_coords)
        print(f"  تم إنشاء مضلع واحد بعد الدمج")
        
    elif isinstance(merged, MultiPolygon):
        for poly in merged.geoms:
            if not poly.is_empty:
                coords = list(poly.exterior.coords)
                cleaned_coords = clean_geometry_coords(coords)
                result_coords_list.append(cleaned_coords)
        print(f"  تم إنشاء {len(result_coords_list)} مضلع بعد الدمج")
    
    return result_coords_list

@bp2.route("/upload", methods=["POST"])
def upload_file():
    global current_file_path
    file = request.files["file"]
    if file:
        current_file_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(current_file_path)
        return jsonify({"status": "uploaded"})
    return jsonify({"status": "error"})

def arc_to_lines(center, radius, start_angle, end_angle, segments=8):
    points = []
    if end_angle < start_angle:
        end_angle += 360
    
    step = (end_angle - start_angle) / segments
    
    for i in range(segments + 1):
        angle = math.radians(start_angle + i * step)
        x = center[0] + radius * math.cos(angle)
        y = center[1] + radius * math.sin(angle)
        points.append((x, y))
    
    cleaned_points = [points[0]]
    for i in range(1, len(points)):
        p1 = Point(cleaned_points[-1])
        p2 = Point(points[i])
        if p1.distance(p2) > 0.01:
            cleaned_points.append(points[i])
    
    return cleaned_points

def collect_red_elements(msp):
    red_elements = []
    
    for e in msp:
        if e.dxf.color != 1:
            continue
            
        if e.dxftype() == "LINE":
            s = e.dxf.start
            e2 = e.dxf.end
            red_elements.append(LineString([(s.x, s.y), (e2.x, e2.y)]))
            
        elif e.dxftype() in ["LWPOLYLINE", "POLYLINE"]:
            pts = [(p[0], p[1]) for p in e.get_points()]
            if e.closed and pts and pts[0] != pts[-1]:
                pts.append(pts[0])
            red_elements.append(LineString(pts))
            
        elif e.dxftype() == "ARC":
            center = (e.dxf.center.x, e.dxf.center.y)
            radius = e.dxf.radius
            start_angle = e.dxf.start_angle
            end_angle = e.dxf.end_angle
            points = arc_to_lines(center, radius, start_angle, end_angle)
            red_elements.append(LineString(points))
            
        elif e.dxftype() == "CIRCLE":
            center = (e.dxf.center.x, e.dxf.center.y)
            radius = e.dxf.radius
            points = arc_to_lines(center, radius, 0, 360)
            if points[0] != points[-1]:
                points.append(points[0])
            red_elements.append(LineString(points))
    
    return red_elements

def create_encorbellement_from_red_lines(red_elements, zone_polygon, distance=1.2, tolerance=0.01):
    if not red_elements:
        return []
    
    print(f"\n=== بدء إنشاء البروزات (الإزاحة بمسافة {distance} للخارج) ===")
    print(f"عدد العناصر الحمراء: {len(red_elements)}")
    
    merged_red = unary_union(red_elements)
    
    if merged_red.is_empty:
        return []
    
    if isinstance(merged_red, MultiLineString):
        merged_red = linemerge(merged_red)
        print("تم دمج الخطوط المتعددة")
    
    lines_to_process = []
    if isinstance(merged_red, LineString):
        lines_to_process = [merged_red]
    elif isinstance(merged_red, MultiLineString):
        lines_to_process = list(merged_red.geoms)
    else:
        print(f"نوع غير متوقع: {type(merged_red)}")
        return []
    
    result_polygons = []
    
    for i, line in enumerate(lines_to_process):
        if line.is_empty or len(line.coords) < 2:
            continue
        
        offset_line = None
        
        try:
            offset_right = line.parallel_offset(distance, 'right', join_style=2, mitre_limit=5.0)
            if not offset_right.is_empty:
                mid_point = offset_right.interpolate(0.5, normalized=True)
                if not zone_polygon.contains(mid_point):
                    offset_line = offset_right
        except:
            pass
        
        if offset_line is None:
            try:
                offset_left = line.parallel_offset(distance, 'left', join_style=2, mitre_limit=5.0)
                if not offset_left.is_empty:
                    mid_point = offset_left.interpolate(0.5, normalized=True)
                    if not zone_polygon.contains(mid_point):
                        offset_line = offset_left
            except:
                pass
        
        if offset_line is None:
            continue
        
        if isinstance(offset_line, MultiLineString):
            longest = max(offset_line.geoms, key=lambda ls: ls.length)
            offset_line = longest
        
        original_coords = list(line.coords)
        offset_coords = list(offset_line.coords)
        
        start_orig = Point(original_coords[0])
        end_orig = Point(original_coords[-1])
        start_off = Point(offset_coords[0])
        end_off = Point(offset_coords[-1])
        
        dist_start_start = start_off.distance(start_orig)
        dist_start_end = start_off.distance(end_orig)
        
        if dist_start_start < dist_start_end:
            poly_coords = offset_coords + original_coords[::-1]
        else:
            poly_coords = offset_coords[::-1] + original_coords[::-1]
        
        try:
            polygon = Polygon(poly_coords)
            
            if polygon.is_valid and not polygon.is_empty and polygon.area > 0.001:
                cleaned_poly = polygon.buffer(0)
                
                if isinstance(cleaned_poly, Polygon):
                    result_polygons.append(list(cleaned_poly.exterior.coords))
                elif isinstance(cleaned_poly, MultiPolygon):
                    for p in cleaned_poly.geoms:
                        if not p.is_empty:
                            result_polygons.append(list(p.exterior.coords))
        except Exception as e:
            continue
    
    return result_polygons

def close_all_gaps_between_red_elements(red_elements, zone_polygon, distance=1.2, gap_tolerance=0.5):
    print("\n=== إغلاق جميع الفجوات بين العناصر الحمراء ===")
    
    if not red_elements:
        return [], []
    
    merged_red = unary_union(red_elements)
    
    if merged_red.is_empty:
        return [], []
    
    lines_to_process = []
    if isinstance(merged_red, LineString):
        lines_to_process = [merged_red]
    elif isinstance(merged_red, MultiLineString):
        merged_red = linemerge(merged_red)
        if isinstance(merged_red, LineString):
            lines_to_process = [merged_red]
        elif isinstance(merged_red, MultiLineString):
            lines_to_process = list(merged_red.geoms)
    else:
        return [], []
    
    print(f"عدد الخطوط بعد الدمج: {len(lines_to_process)}")
    
    side_lines = []
    gap_fillers = []
    
    for line in lines_to_process:
        if line.is_empty or len(line.coords) < 2:
            continue
        
        print(f"\nمعالجة خط بطول: {line.length:.2f}")
        
        try:
            right_offset = line.parallel_offset(distance, 'right', join_style=2, mitre_limit=5.0)
            if not right_offset.is_empty and isinstance(right_offset, LineString):
                mid_point = right_offset.interpolate(0.5, normalized=True)
                if not zone_polygon.contains(mid_point):
                    side_lines.append(('right', right_offset))
                    print(f"  تم إنشاء الخط الخارجي بطول: {right_offset.length:.2f}")
            
            left_offset = line.parallel_offset(distance, 'left', join_style=2, mitre_limit=5.0)
            if not left_offset.is_empty and isinstance(left_offset, LineString):
                mid_point = left_offset.interpolate(0.5, normalized=True)
                if not zone_polygon.contains(mid_point):
                    side_lines.append(('left', left_offset))
                    print(f"  تم إنشاء الخط الخارجي بطول: {left_offset.length:.2f}")
                    
        except Exception as e:
            print(f"خطأ في إنشاء الخط المتوازي: {e}")
            continue
    
    if not side_lines:
        print("لم يتم إنشاء أي خطوط جانبية")
        return [], []
    
    print(f"\n=== إغلاق الفجوات بين {len(side_lines)} خط جانبي ===")
    
    all_endpoints = []
    for idx, (side_type, side_line) in enumerate(side_lines):
        coords = list(side_line.coords)
        start_point = Point(coords[0])
        end_point = Point(coords[-1])
        all_endpoints.append((idx, 'start', start_point, coords[0]))
        all_endpoints.append((idx, 'end', end_point, coords[-1]))
    
    used_indices = set()
    for i in range(len(all_endpoints)):
        if i in used_indices:
            continue
            
        idx1, type1, point1, coord1 = all_endpoints[i]
        best_match = None
        best_dist = float('inf')
        
        for j in range(len(all_endpoints)):
            if i == j or j in used_indices:
                continue
                
            idx2, type2, point2, coord2 = all_endpoints[j]
            
            if idx1 == idx2:
                continue
            
            dist = point1.distance(point2)
            
            if dist < gap_tolerance and dist < best_dist:
                best_dist = dist
                best_match = j
        
        if best_match is not None:
            _, _, _, coord2 = all_endpoints[best_match]
            connecting_line = LineString([coord1, coord2])
            gap_fillers.append(connecting_line)
            used_indices.add(i)
            used_indices.add(best_match)
            print(f"  تم إغلاق فجوة بين الخط {idx1+1} ({type1}) والخط {all_endpoints[best_match][0]+1} ({all_endpoints[best_match][1]}) - المسافة: {best_dist:.3f}")
    
    print(f"\nتم إنشاء {len(side_lines)} خط جانبي و {len(gap_fillers)} خط لملء الفجوات")
    
    return side_lines, gap_fillers

def create_acroterion_wall_inward(zone_polygon_coords, red_polygons_coords_list, wall_thickness=0.2, height=1.2, tolerance=0.01):
    try:
        print("\n=== بدء إنشاء جدار الأكروتير (باتجاه الداخل) ===")
        
        if len(zone_polygon_coords) < 3:
            return None, None
        
        zone_coords_clean = clean_geometry_coords(zone_polygon_coords, tolerance)
        zone_polygon = Polygon(zone_coords_clean)
        
        red_polygons = []
        for red_coords in red_polygons_coords_list:
            if len(red_coords) >= 3:
                try:
                    red_coords_clean = clean_geometry_coords(red_coords, tolerance)
                    red_poly = Polygon(red_coords_clean)
                    
                    if red_poly.is_valid and not red_poly.is_empty and red_poly.area > 0.001:
                        red_polygons.append(red_poly)
                except:
                    continue
        
        if red_polygons:
            all_polys = [zone_polygon] + red_polygons
            combined_polygon = unary_union(all_polys)
            combined_polygon = combined_polygon.buffer(0)
            
            if isinstance(combined_polygon, Polygon):
                outer_coords_raw = list(combined_polygon.exterior.coords)
            elif isinstance(combined_polygon, MultiPolygon):
                main_polygon = max(combined_polygon.geoms, key=lambda p: p.area)
                outer_coords_raw = list(main_polygon.exterior.coords)
            else:
                outer_coords_raw = zone_coords_clean
        else:
            outer_coords_raw = zone_coords_clean
        
        outer_coords = clean_geometry_coords(outer_coords_raw, tolerance)
        
        if len(outer_coords) < 3:
            return None, None
        
        outer_polygon = Polygon(outer_coords)
        
        try:
            inner_polygon = outer_polygon.buffer(-wall_thickness, join_style=2)
            inner_polygon = inner_polygon.buffer(0)
            
            if inner_polygon.is_empty or not inner_polygon.is_valid:
                print("فشل في إنشاء الجدار الداخلي")
                return None, None
            
            if isinstance(inner_polygon, Polygon):
                inner_coords_raw = list(inner_polygon.exterior.coords)
            elif isinstance(inner_polygon, MultiPolygon):
                main_inner = max(inner_polygon.geoms, key=lambda p: p.area)
                inner_coords_raw = list(main_inner.exterior.coords)
            else:
                return None, None
            
        except Exception as e:
            print(f"خطأ في إنشاء الجدار الداخلي: {e}")
            return None, None
        
        inner_coords = clean_geometry_coords(inner_coords_raw, tolerance)
        
        if len(inner_coords) < 3:
            return None, None
        
        outer_line = LineString(outer_coords)
        inner_line = LineString(inner_coords)
        
        simplified_outer = outer_line.simplify(tolerance * 2, preserve_topology=True)
        simplified_inner = inner_line.simplify(tolerance * 2, preserve_topology=True)
        
        final_outer_coords = list(simplified_outer.coords)
        final_inner_coords = list(simplified_inner.coords)
        
        outer_poly = Polygon(final_outer_coords)
        inner_poly = Polygon(final_inner_coords)
        
        if not outer_poly.exterior.is_ccw:
            final_outer_coords = list(reversed(final_outer_coords))
        
        if not inner_poly.exterior.is_ccw:
            final_inner_coords = list(reversed(final_inner_coords))
        
        print(f"تم إنشاء جدار الأكروتير بسماكة {wall_thickness} متر للداخل")
        return final_outer_coords, final_inner_coords
        
    except Exception as e:
        print(f"\n!!! خطأ في create_acroterion_wall_inward: {e}")
        return None, None

# ============================================================
# دوال الـ 3D لـ RED_BLOCK (تبقى كما هي)
# ============================================================

def create_organized_red_block_faces(block, points_2d, height, layer, color, direction='right_to_left'):
    n = len(points_2d)
    
    if n < 3:
        return 0
    
    bottom_3d = [(x, y, 0) for x, y in points_2d]
    top_3d = [(x, y, height) for x, y in points_2d]
    
    face_count = 0
    
    if direction == 'right_to_left':
        for i in range(n - 1):
            idx = n - 2 - i
            p1_bottom = bottom_3d[idx]
            p2_bottom = bottom_3d[idx + 1]
            p1_top = top_3d[idx]
            p2_top = top_3d[idx + 1]
            
            if (p1_bottom != p2_bottom and p1_bottom != p1_top):
                block.add_3dface(
                    [p1_bottom, p2_bottom, p2_top, p1_top],
                    dxfattribs={"layer": layer, "color": color}
                )
                face_count += 1
    else:
        for i in range(n - 1):
            p1_bottom = bottom_3d[i]
            p2_bottom = bottom_3d[i + 1]
            p1_top = top_3d[i]
            p2_top = top_3d[i + 1]
            
            if (p1_bottom != p2_bottom and p1_bottom != p1_top):
                block.add_3dface(
                    [p1_bottom, p2_bottom, p2_top, p1_top],
                    dxfattribs={"layer": layer, "color": color}
                )
                face_count += 1
    
    if n >= 3:
        p_last_bottom = bottom_3d[-1]
        p_first_bottom = bottom_3d[0]
        p_last_top = top_3d[-1]
        p_first_top = top_3d[0]
        
        block.add_3dface(
            [p_last_bottom, p_first_bottom, p_first_top, p_last_top],
            dxfattribs={"layer": layer, "color": color}
        )
        face_count += 1
    
    return face_count

def create_red_block_A_3d(doc, msp, red_polygon_coords, height, base_z, zone_index):
    if not red_polygon_coords or len(red_polygon_coords) < 3:
        return
    
    cleaned_coords = clean_geometry_coords(red_polygon_coords, 0.05)
    
    if len(cleaned_coords) < 3:
        return
    
    coords_hash = hash(tuple(tuple(p) for p in cleaned_coords))
    block_name = f"RED_BLOCK_A_{height}_{zone_index}_{base_z}_{coords_hash}"
    
    if block_name not in doc.blocks:
        block = doc.blocks.new(name=block_name)
        
        face_count = create_organized_red_block_faces(
            block,
            cleaned_coords,
            height,
            "RED_BLOCK_A",
            1,
            direction='right_to_left'
        )
        
        print(f"  RED_BLOCK_A: تم إنشاء {face_count} وجه جانبي (بدون علوي/سفلي)")
    
    msp.add_blockref(
        block_name,
        (0, 0, base_z),
        dxfattribs={"layer": "RED_BLOCK_A", "color": 1}
    )

def process_red_blocks_A(doc3d, msp3d, merged_red_block, zone_index, base_z, height=3.0):
    if not merged_red_block:
        return
    
    if "RED_BLOCK_A" not in doc3d.layers:
        doc3d.layers.new(name="RED_BLOCK_A", dxfattribs={"color": 1})
    
    for red_poly_coords in merged_red_block:
        if len(red_poly_coords) >= 3:
            create_red_block_A_3d(
                doc3d,
                msp3d,
                red_poly_coords,
                height,
                base_z,
                zone_index
            )

def create_top_bottom_faces_from_side_lines(block, original_coords, offset_coords, height, layer, color):
    face_count = 0
    
    n_orig = len(original_coords)
    n_off = len(offset_coords)
    
    if n_orig < 2 or n_off < 2:
        return 0
    
    print(f"    ربط {min(n_orig, n_off)} زوج من النقاط لإنشاء الوجوه")
    
    for i in range(min(n_orig, n_off) - 1):
        p1_orig_3d_top = (original_coords[i][0], original_coords[i][1], height)
        p2_orig_3d_top = (original_coords[i+1][0], original_coords[i+1][1], height)
        p1_off_3d_top = (offset_coords[i][0], offset_coords[i][1], height)
        p2_off_3d_top = (offset_coords[i+1][0], offset_coords[i+1][1], height)
        
        block.add_3dface(
            [p1_orig_3d_top, p1_off_3d_top, p2_off_3d_top, p2_orig_3d_top],
            dxfattribs={"layer": layer, "color": color}
        )
        face_count += 1
    
    for i in range(min(n_orig, n_off) - 1):
        p1_orig_3d_bottom = (original_coords[i][0], original_coords[i][1], 0)
        p2_orig_3d_bottom = (original_coords[i+1][0], original_coords[i+1][1], 0)
        p1_off_3d_bottom = (offset_coords[i][0], offset_coords[i][1], 0)
        p2_off_3d_bottom = (offset_coords[i+1][0], offset_coords[i+1][1], 0)
        
        block.add_3dface(
            [p1_orig_3d_bottom, p1_off_3d_bottom, p2_off_3d_bottom, p2_orig_3d_bottom],
            dxfattribs={"layer": layer, "color": color}
        )
        face_count += 1
    
    return face_count

def create_red_block_B_3d(doc, msp, original_red_coords, offset_red_coords, height, base_z, zone_index):
    if not original_red_coords or len(original_red_coords) < 2:
        return
    
    if not offset_red_coords or len(offset_red_coords) < 2:
        return
    
    orig_clean = clean_geometry_coords(original_red_coords, 0.05)
    off_clean = clean_geometry_coords(offset_red_coords, 0.05)
    
    if len(orig_clean) > 1 and orig_clean[0] == orig_clean[-1]:
        orig_clean = orig_clean[:-1]
    if len(off_clean) > 1 and off_clean[0] == off_clean[-1]:
        off_clean = off_clean[:-1]
    
    if len(orig_clean) < 2 or len(off_clean) < 2:
        return
    
    coords_hash = hash((tuple(tuple(p) for p in orig_clean), tuple(tuple(p) for p in off_clean)))
    block_name = f"RED_BLOCK_B_{height}_{zone_index}_{base_z}_{coords_hash}"
    
    if block_name not in doc.blocks:
        block = doc.blocks.new(name=block_name)
        
        face_count = 0
        
        orig_bottom = [(x, y, 0) for x, y in orig_clean]
        orig_top = [(x, y, height) for x, y in orig_clean]
        off_bottom = [(x, y, 0) for x, y in off_clean]
        off_top = [(x, y, height) for x, y in off_clean]
        
        n = min(len(orig_clean), len(off_clean))
        
        for i in range(n - 1):
            block.add_3dface(
                [orig_bottom[i], off_bottom[i], off_top[i], orig_top[i]],
                dxfattribs={"layer": "RED_BLOCK_B", "color": 1}
            )
            face_count += 1
        
        top_bottom_faces = create_top_bottom_faces_from_side_lines(
            block, orig_clean, off_clean, height, "RED_BLOCK_B", 1
        )
        face_count += top_bottom_faces
        
        print(f"  RED_BLOCK_B: تم إنشاء {face_count} وجه (جوانب + {top_bottom_faces} علوي/سفلي)")
    
    msp.add_blockref(
        block_name,
        (0, 0, base_z),
        dxfattribs={"layer": "RED_BLOCK_B", "color": 1}
    )

def process_red_blocks_B(doc3d, msp3d, merged_red_block, current_zone_red, zone_polygon, zone_index, base_z, height=3.0, distance=1.2):
    if not merged_red_block:
        return
    
    if "RED_BLOCK_B" not in doc3d.layers:
        doc3d.layers.new(name="RED_BLOCK_B", dxfattribs={"color": 1})
    
    if not current_zone_red:
        return
    
    merged_original = unary_union(current_zone_red)
    
    if merged_original.is_empty:
        return
    
    original_lines = []
    if isinstance(merged_original, LineString):
        original_lines = [merged_original]
    elif isinstance(merged_original, MultiLineString):
        merged_original = linemerge(merged_original)
        if isinstance(merged_original, LineString):
            original_lines = [merged_original]
        elif isinstance(merged_original, MultiLineString):
            original_lines = list(merged_original.geoms)
    
    print(f"\n=== إنشاء RED_BLOCK_B بإزاحة للخارج بمسافة {distance} متر ===")
    
    for orig_line in original_lines:
        if orig_line.is_empty or len(orig_line.coords) < 2:
            continue
        
        try:
            offset_line = None
            
            right_offset = orig_line.parallel_offset(distance, 'right', join_style=2, mitre_limit=5.0)
            if not right_offset.is_empty:
                if isinstance(right_offset, LineString):
                    mid_point = right_offset.interpolate(0.5, normalized=True)
                    if not zone_polygon.contains(mid_point):
                        offset_line = right_offset
            
            if offset_line is None:
                left_offset = orig_line.parallel_offset(distance, 'left', join_style=2, mitre_limit=5.0)
                if not left_offset.is_empty:
                    if isinstance(left_offset, LineString):
                        mid_point = left_offset.interpolate(0.5, normalized=True)
                        if not zone_polygon.contains(mid_point):
                            offset_line = left_offset
            
            if offset_line is None or offset_line.is_empty:
                print(f"  تحذير: لا يمكن إنشاء إزاحة للخارج للخط")
                continue
            
            if isinstance(offset_line, LineString):
                offset_coords = list(offset_line.coords)
            elif isinstance(offset_line, MultiLineString):
                longest = max(offset_line.geoms, key=lambda ls: ls.length)
                offset_coords = list(longest.coords)
            else:
                continue
            
            original_coords = list(orig_line.coords)
            
            original_coords = clean_geometry_coords(original_coords, 0.05)
            offset_coords = clean_geometry_coords(offset_coords, 0.05)
            
            if len(original_coords) > 1 and original_coords[0] == original_coords[-1]:
                original_coords = original_coords[:-1]
            if len(offset_coords) > 1 and offset_coords[0] == offset_coords[-1]:
                offset_coords = offset_coords[:-1]
            
            print(f"  إنشاء RED_BLOCK_B: خط أصلي {len(original_coords)} نقطة، خط مزاح {len(offset_coords)} نقطة")
            
            create_red_block_B_3d(
                doc3d, msp3d, original_coords, offset_coords, height, base_z, zone_index
            )
                
        except Exception as e:
            print(f"  خطأ في إنشاء RED_BLOCK_B: {e}")
            continue

# ============================================================
# دوال مساعدة للتعامل مع الأقواس
# ============================================================

from shapely.geometry import Polygon
from shapely.ops import triangulate

def densify_polyline(coords, step=0.5):
    if len(coords) < 2:
        return coords

    def point_line_distance(p, a, b):
        px, py = p
        ax, ay = a
        bx, by = b

        dx = bx - ax
        dy = by - ay

        if dx == 0 and dy == 0:
            return ((px - ax)**2 + (py - ay)**2) ** 0.5

        t = ((px - ax) * dx + (py - ay) * dy) / (dx*dx + dy*dy)
        t = max(0, min(1, t))

        proj_x = ax + t * dx
        proj_y = ay + t * dy

        return ((px - proj_x)**2 + (py - proj_y)**2) ** 0.5

    tolerance = 0.03

    result = []

    def subdivide(p1, p2):
        mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)

        dist = point_line_distance(mid, p1, p2)

        if dist > tolerance:
            subdivide(p1, mid)
            subdivide(mid, p2)
        else:
            result.append(p1)

    for i in range(len(coords)):
        p1 = coords[i]
        p2 = coords[(i + 1) % len(coords)]
        subdivide(p1, p2)

    return result

def create_side_faces(block, points_2d, z_top, z_bottom, layer, color):
    face_count = 0
    n = len(points_2d)

    for i in range(n):
        j = (i + 1) % n

        p1b = (points_2d[i][0], points_2d[i][1], z_bottom)
        p2b = (points_2d[j][0], points_2d[j][1], z_bottom)
        p1t = (points_2d[i][0], points_2d[i][1], z_top)
        p2t = (points_2d[j][0], points_2d[j][1], z_top)

        block.add_3dface(
            [p1b, p2b, p2t, p2t],
            dxfattribs={"layer": layer, "color": color}
        )

        block.add_3dface(
            [p1b, p2t, p1t, p1t],
            dxfattribs={"layer": layer, "color": color}
        )

        face_count += 2

    return face_count

def create_top_bottom_triangulated_faces(block, points_2d, z_top, z_bottom, layer, color):
    face_count = 0

    polygon = Polygon(points_2d)

    if not polygon.is_valid or polygon.area <= 0:
        return 0

    triangles = triangulate(polygon)

    for tri in triangles:
        if not tri.centroid.within(polygon):
            continue

        coords = list(tri.exterior.coords)[:-1]

        if len(coords) != 3:
            continue

        a = coords[0]
        b = coords[1]
        c = coords[2]

        top_a = (a[0], a[1], z_top)
        top_b = (b[0], b[1], z_top)
        top_c = (c[0], c[1], z_top)

        bottom_a = (a[0], a[1], z_bottom)
        bottom_b = (b[0], b[1], z_bottom)
        bottom_c = (c[0], c[1], z_bottom)

        block.add_3dface(
            [top_a, top_b, top_c, top_c],
            dxfattribs={"layer": layer, "color": color}
        )

        block.add_3dface(
            [bottom_c, bottom_b, bottom_a, bottom_a],
            dxfattribs={"layer": layer, "color": color}
        )

        face_count += 2

    return face_count

# ============================================================
# دالة جديدة: topdown_niveau
# الوظيفة: إنشاء الوجوه العلوية والسفلية للمستويات فقط
# ============================================================

def topdown_niveau(doc, msp, coords, height, layer, color, zone_index, base_z=0):
    """
    هذه الدالة مسؤولة عن إنشاء الوجوه العلوية والسفلية (TOP & BOTTOM)
    للمستويات (الطوابق) فقط، ويتم وضعها في كالque منفصل "topdown"
    """
    if abs(height - 1.2) < 0.001:
        return

    coords_tuple = tuple(tuple(p) for p in coords)
    coords_hash = hash(coords_tuple)
    block_name = f"TOPDOWN_{layer}_{height}_{zone_index}_{base_z}_{coords_hash}"

    if block_name not in doc.blocks:
        block = doc.blocks.new(name=block_name)

        # تنظيف الإحداثيات
        coords_clean = clean_geometry_coords(coords, 0.05)
        
        if len(coords_clean) < 3:
            return

        face_count = 0

        # ========================================
        # إنشاء الوجه العلوي (TOP FACE)
        # ========================================
        try:
            # إنشاء مضلع للوجه العلوي
            top_polygon = Polygon(coords_clean)
            
            if top_polygon.is_valid and not top_polygon.is_empty and top_polygon.area > 0.001:
                # تقسيم المضلع إلى مثلثات
                triangles = triangulate(top_polygon)
                
                for tri in triangles:
                    if not tri.centroid.within(top_polygon):
                        continue
                    
                    tri_coords = list(tri.exterior.coords)[:-1]
                    if len(tri_coords) == 3:
                        # رفع النقاط إلى الارتفاع المطلوب
                        tri_3d = [(x, y, height) for x, y in tri_coords]
                        
                        block.add_3dface(
                            [tri_3d[0], tri_3d[1], tri_3d[2], tri_3d[0]],
                            dxfattribs={"layer": "topdown", "color": color}
                        )
                        face_count += 1
        except Exception as e:
            print(f"  تحذير: فشل في إنشاء الوجه العلوي - {e}")

        # ========================================
        # إنشاء الوجه السفلي (BOTTOM FACE)
        # ========================================
        try:
            # إنشاء مضلع للوجه السفلي
            bottom_polygon = Polygon(coords_clean)
            
            if bottom_polygon.is_valid and not bottom_polygon.is_empty and bottom_polygon.area > 0.001:
                # تقسيم المضلع إلى مثلثات
                triangles = triangulate(bottom_polygon)
                
                for tri in triangles:
                    if not tri.centroid.within(bottom_polygon):
                        continue
                    
                    tri_coords = list(tri.exterior.coords)[:-1]
                    if len(tri_coords) == 3:
                        # النقاط في المستوى Z=0
                        tri_3d = [(x, y, 0) for x, y in tri_coords]
                        
                        # عكس الترتيب للوجه السفلي
                        block.add_3dface(
                            [tri_3d[2], tri_3d[1], tri_3d[0], tri_3d[0]],
                            dxfattribs={"layer": "topdown", "color": color}
                        )
                        face_count += 1
        except Exception as e:
            print(f"  تحذير: فشل في إنشاء الوجه السفلي - {e}")

        print(f"  topdown_niveau: تم إنشاء {face_count} وجه (علوي + سفلي) في كالك 'topdown'")

    # إضافة مرجع الكتلة
    msp.add_blockref(
        block_name,
        (0, 0, base_z),
        dxfattribs={"layer": "topdown", "color": color}
    )

# ============================================================
# دالة create_3d_stack المعدلة (بدون وجوه علوية/سفلية)
# ============================================================

def create_3d_stack(doc, msp, coords, height, layer, color, zone_index, base_z=0):
    """
    هذه الدالة مسؤولة عن إنشاء الوجوه الجانبية فقط للمستويات
    (بدون وجوه علوية وسفلية)
    """
    if abs(height - 1.2) < 0.001:
        return

    coords_tuple = tuple(tuple(p) for p in coords)
    coords_hash = hash(coords_tuple)
    block_name = f"{layer}_{height}_{zone_index}_{base_z}_{coords_hash}"

    if block_name not in doc.blocks:
        block = doc.blocks.new(name=block_name)

        bottom = [(x, y, 0) for x, y in coords]
        top = [(x, y, height) for x, y in coords]

        n = len(coords)
        face_count = 0

        # إنشاء الوجوه الجانبية ONLY (بدون علوية وسفلية)
        for i in range(n - 1):
            p1 = bottom[i]
            p2 = bottom[i + 1]
            p3 = top[i + 1]
            p4 = top[i]

            block.add_3dface(
                [p1, p2, p3, p4],
                dxfattribs={"layer": layer, "color": color}
            )
            face_count += 1

        # إغلاق الوجه الأخير بين آخر نقطة وأول نقطة
        if n >= 3:
            p1 = bottom[-1]
            p2 = bottom[0]
            p3 = top[0]
            p4 = top[-1]
            
            block.add_3dface(
                [p1, p2, p3, p4],
                dxfattribs={"layer": layer, "color": color}
            )
            face_count += 1
        
        print(f"  {layer}: تم إنشاء {face_count} وجه جانبي فقط (بدون علوي/سفلي)")

    msp.add_blockref(
        block_name,
        (0, 0, base_z),
        dxfattribs={"layer": layer, "color": color}
    )

# ============================================================
# دوال الرصيف والطريق (تبقى كما هي)
# ============================================================

def create_sidewalk_B_3d(doc, msp, polygon_coords, base_z, zone_index):
    height = 0.15

    if not polygon_coords or len(polygon_coords) < 3:
        return

    coords = clean_geometry_coords(polygon_coords, 0.01)

    if coords[0] == coords[-1]:
        coords = coords[:-1]

    if len(coords) < 3:
        return

    coords = densify_polyline(coords, step=0.25)

    block_name = f"SIDEWALK_B_{zone_index}_{base_z}_{hash(tuple(map(tuple, coords)))}"

    if block_name not in doc.blocks:
        block = doc.blocks.new(name=block_name)

        face_count = 0

        face_count += create_side_faces(
            block,
            coords,
            height,
            0,
            "SIDEWALK_B",
            41,
        )

        face_count += create_top_bottom_triangulated_faces(
            block,
            coords,
            height,
            0,
            "SIDEWALK_B",
            41,
        )

        print(f"SIDEWALK_B: {face_count} وجه (جوانب + علوي/سفلي)")

    msp.add_blockref(
        block_name,
        (0, 0, base_z),
        dxfattribs={"layer": "SIDEWALK_B", "color": 41},
    )

def create_road_B_3d(doc, msp, polygon_coords, base_z, zone_index):
    height = 0.05

    if not polygon_coords or len(polygon_coords) < 3:
        return

    coords = clean_geometry_coords(polygon_coords, 0.01)

    if coords[0] == coords[-1]:
        coords = coords[:-1]

    if len(coords) < 3:
        return

    coords = densify_polyline(coords, step=0.25)

    block_name = f"ROAD_B_{zone_index}_{base_z}_{hash(tuple(map(tuple, coords)))}"

    if block_name not in doc.blocks:
        block = doc.blocks.new(name=block_name)

        face_count = 0

        face_count += create_side_faces(
            block,
            coords,
            height,
            0,
            "ROAD_B",
            251,
        )

        face_count += create_top_bottom_triangulated_faces(
            block,
            coords,
            height,
            0,
            "ROAD_B",
            251,
        )

        print(f"ROAD_B: {face_count} وجه (جوانب + علوي/سفلي)")

    msp.add_blockref(
        block_name,
        (0, 0, base_z),
        dxfattribs={"layer": "ROAD_B", "color": 251},
    )

# ============================================================
# دالة الأكروتير (تبقى كما هي مع وجوه علوية وسفلية)
# ============================================================

def create_hollow_wall(doc, msp, outer_coords, inner_coords, height, layer, color, block_name, base_z=0, red_polygons_coords_list=None):
    """
    هذه الدالة تبقى كما هي للأكروتير - تحتوي على وجوه علوية وسفلية
    """
    if red_polygons_coords_list is None:
        red_polygons_coords_list = []
    
    if block_name not in doc.blocks:
        block = doc.blocks.new(name=block_name)
        
        outer_coords_clean = clean_geometry_coords(outer_coords)
        inner_coords_clean = clean_geometry_coords(inner_coords)
        
        outer_bottom = [(x, y, 0) for x, y in outer_coords_clean]
        outer_top = [(x, y, height) for x, y in outer_coords_clean]
        inner_bottom = [(x, y, 0) for x, y in inner_coords_clean]
        inner_top = [(x, y, height) for x, y in inner_coords_clean]
        
        n_outer = len(outer_coords_clean)
        n_inner = len(inner_coords_clean)
        
        print(f"\n--- إنشاء الأكروتير مع وجوه كاملة (علوية + سفلية + جانبية) ---")
        
        # الوجوه الجانبية الخارجية
        for i in range(n_outer - 1):
            p1 = outer_bottom[i]
            p2 = outer_bottom[i + 1]
            p3 = outer_top[i + 1]
            p4 = outer_top[i]
            block.add_3dface([p1, p2, p3, p4], dxfattribs={"layer": layer, "color": color})
        
        # الوجوه الجانبية الداخلية
        for i in range(n_inner - 1):
            p1 = inner_bottom[i]
            p2 = inner_bottom[i + 1]
            p3 = inner_top[i + 1]
            p4 = inner_top[i]
            block.add_3dface([p1, p2, p3, p4], dxfattribs={"layer": layer, "color": color})
        
        # الوجوه العلوية (TOP)
        create_organized_top_faces(
            block,
            outer_coords_clean,
            inner_coords_clean,
            height,
            layer,
            color
        )
        
    msp.add_blockref(block_name, (0, 0, base_z), dxfattribs={"layer": layer, "color": color})

def create_organized_top_faces(block, outer_points, inner_points, height, layer, color):
    print("\n--- إنشاء الوجوه العلوية المنظمة ---")
    
    from shapely.geometry import Point as ShapelyPoint
    
    n_outer = len(outer_points)
    n_inner = len(inner_points)
    
    print(f"عدد النقاط الخارجية: {n_outer}")
    print(f"عدد النقاط الداخلية: {n_inner}")
    
    start_outer = outer_points[0]
    start_outer_shapely = ShapelyPoint(start_outer)
    
    best_inner_idx = 0
    min_dist = float('inf')
    
    for i, pt in enumerate(inner_points):
        dist = start_outer_shapely.distance(ShapelyPoint(pt))
        if dist < min_dist:
            min_dist = dist
            best_inner_idx = i
    
    print(f"نقطة البداية: خارجية[0] مع داخلية[{best_inner_idx}] (مسافة {min_dist:.4f})")
    
    inner_reordered = inner_points[best_inner_idx:] + inner_points[:best_inner_idx]
    
    distance_matrix = []
    for i, outer_pt in enumerate(outer_points):
        outer_shapely = ShapelyPoint(outer_pt)
        distances = []
        for j, inner_pt in enumerate(inner_reordered):
            dist = outer_shapely.distance(ShapelyPoint(inner_pt))
            distances.append(dist)
        distance_matrix.append(distances)
    
    outer_to_inner_map = []
    for i in range(n_outer):
        best_j = 0
        best_dist = float('inf')
        for j in range(len(inner_reordered)):
            dist = distance_matrix[i][j]
            if dist < best_dist:
                best_dist = dist
                best_j = j
        outer_to_inner_map.append(best_j)
    
    print(f"خريطة الربط: {outer_to_inner_map}")
    
    face_count = 0
    for i in range(n_outer - 1):
        p_outer1_3d = (outer_points[i][0], outer_points[i][1], height)
        p_outer2_3d = (outer_points[i + 1][0], outer_points[i + 1][1], height)
        
        j1 = outer_to_inner_map[i]
        j2 = outer_to_inner_map[i + 1]
        
        p_inner1_3d = (inner_reordered[j1][0], inner_reordered[j1][1], height)
        p_inner2_3d = (inner_reordered[j2][0], inner_reordered[j2][1], height)
        
        points_set = {
            (p_outer1_3d[0], p_outer1_3d[1]),
            (p_outer2_3d[0], p_outer2_3d[1]),
            (p_inner1_3d[0], p_inner1_3d[1]),
            (p_inner2_3d[0], p_inner2_3d[1])
        }
        
        if len(points_set) >= 3:
            block.add_3dface([p_outer1_3d, p_outer2_3d, p_inner2_3d, p_inner1_3d],
                             dxfattribs={"layer": layer, "color": color})
            face_count += 1
    
    # الوجوه السفلية (BOTTOM) - نضيفها أيضاً للأكروتير
    for i in range(n_outer - 1):
        p_outer1_3d_bottom = (outer_points[i][0], outer_points[i][1], 0)
        p_outer2_3d_bottom = (outer_points[i + 1][0], outer_points[i + 1][1], 0)
        
        j1 = outer_to_inner_map[i]
        j2 = outer_to_inner_map[i + 1]
        
        p_inner1_3d_bottom = (inner_reordered[j1][0], inner_reordered[j1][1], 0)
        p_inner2_3d_bottom = (inner_reordered[j2][0], inner_reordered[j2][1], 0)
        
        points_set = {
            (p_outer1_3d_bottom[0], p_outer1_3d_bottom[1]),
            (p_outer2_3d_bottom[0], p_outer2_3d_bottom[1]),
            (p_inner1_3d_bottom[0], p_inner1_3d_bottom[1]),
            (p_inner2_3d_bottom[0], p_inner2_3d_bottom[1])
        }
        
        if len(points_set) >= 3:
            block.add_3dface([p_outer1_3d_bottom, p_outer2_3d_bottom, p_inner2_3d_bottom, p_inner1_3d_bottom],
                             dxfattribs={"layer": layer, "color": color})
            face_count += 1
    
    print(f"تم إنشاء {face_count} وجه (علوي + سفلي)")
    return face_count

def triangulate_polygon(points_3d):
    triangles = []
    n = len(points_3d)
    
    if n < 3:
        return triangles
    
    if n == 3:
        triangles.append(points_3d)
        return triangles
    
    if n == 4:
        triangles.append(points_3d)
        return triangles
    
    first_point = points_3d[0]
    
    for i in range(1, n - 1):
        triangle = [first_point, points_3d[i], points_3d[i + 1]]
        triangles.append(triangle)
    
    return triangles

def extract_zone_name(text):
    text = text.strip()
    
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    
    if 'etg' in text.lower():
        text = text.lower().split('etg')[0].strip()
    
    import re
    match = re.search(r'"[\d\.]+"', text)
    if match:
        text = text.replace(match.group(0), '').strip()
    
    return text

# ============================================================
# الدالة الرئيسية generate_2d3d
# ============================================================

@bp2.route("/generate_2d3d", methods=["POST"])
def generate_2d3d():
    global current_file_path
    if not current_file_path:
        return "Upload DXF first"

    doc = ezdxf.readfile(current_file_path)
    msp = doc.modelspace()
    doc3d = ezdxf.new()
    msp3d = doc3d.modelspace()
    doc2d = ezdxf.new()
    msp2d = doc2d.modelspace()
    
    if "ArialStyle" not in doc2d.styles:
        doc2d.styles.new("ArialStyle", dxfattribs={"font": "arial.ttf"})

    all_elements = []
    for e in msp:
        if e.dxftype() == "LINE":
            s = e.dxf.start
            e2 = e.dxf.end
            all_elements.append(LineString([(s.x, s.y), (e2.x, e2.y)]))
        elif e.dxftype() in ["LWPOLYLINE", "POLYLINE"]:
            pts = [(p[0], p[1]) for p in e.get_points()]
            if e.closed and pts and pts[0] != pts[-1]:
                pts.append(pts[0])
            all_elements.append(LineString(pts))
        elif e.dxftype() == "ARC":
            center = (e.dxf.center.x, e.dxf.center.y)
            radius = e.dxf.radius
            start_angle = e.dxf.start_angle
            end_angle = e.dxf.end_angle
            points = arc_to_lines(center, radius, start_angle, end_angle)
            all_elements.append(LineString(points))
        elif e.dxftype() == "CIRCLE":
            center = (e.dxf.center.x, e.dxf.center.y)
            radius = e.dxf.radius
            points = arc_to_lines(center, radius, 0, 360)
            if points[0] != points[-1]:
                points.append(points[0])
            all_elements.append(LineString(points))

    red_elements = collect_red_elements(msp)

    tolerance = 0.01
    merged_all = unary_union([snap(line, unary_union(all_elements), tolerance) for line in all_elements])
    polygons = list(polygonize(merged_all))

    zone_index = 0
    processed_zones = set()

    for poly in polygons:
        zone_index += 1
        coords = list(poly.exterior.coords)
        zone_name = None
        zone_color = 7
        extrude_height = 0
        floors = 0
        text_x = None
        text_y = None
        block_heights = None
        has_etg = False

        for e in msp:
            if e.dxftype() == "TEXT":
                x, y, *_ = e.dxf.insert
                pt = Point(x, y)
                if poly.contains(pt):
                    txt = e.dxf.text.strip()
                    col = e.dxf.color
                    
                    zone_name_clean = extract_zone_name(txt)
                    
                    print(f"  Texte trouvé dans la zone: '{txt}' -> nom extrait: '{zone_name_clean}'")
                    
                    if zone_name_clean == "trottoir":
                        zone_name = "trottoir"
                        zone_color = col
                        text_x = x
                        text_y = y
                        extrude_height = 0.15
                        print(f"    -> Zone spéciale: Trottoir (hauteur=0.15)")
                        break
                    elif zone_name_clean == "voie":
                        zone_name = "voie"
                        zone_color = col
                        text_x = x
                        text_y = y
                        extrude_height = 0.05
                        print(f"    -> Zone spéciale: voie (hauteur=0.05)")
                        break
                    else:
                        r_match = re.match(r"R\+(\d+)$", txt.strip(), re.IGNORECASE)
                        if r_match:
                            floors = int(r_match.group(1))
                            zone_name = zone_name_clean if zone_name_clean else "IMM"
                            zone_color = col
                            text_x = x
                            text_y = y
                            base_z_start = 4.5
                            block_heights = []
                            for i in range(floors):
                                z = base_z_start + i * 3
                                h = 3
                                block_heights.append((z, h))
                            has_etg = True
                            print(f"    -> Détecté R+{floors} - Acrotère activé")
                            break
                        
                        etg_match = re.search(r'"([\d\.]+)"(\d+)etg', txt.lower())
                        if etg_match:
                            extrude_height = float(etg_match.group(1))
                            floors = int(etg_match.group(2))
                            zone_name = zone_name_clean if zone_name_clean else "IMM"
                            zone_color = col
                            text_x = x
                            text_y = y
                            has_etg = True
                            print(f"    -> Détecté hauteur={extrude_height}, {floors}etg - Acrotère activé")
                            break
                        
                        h_search = re.search(r'"([\d\.]+)"', txt)
                        if h_search:
                            extrude_height = float(h_search.group(1))
                            zone_name = zone_name_clean if zone_name_clean else "IMM"
                            zone_color = col
                            text_x = x
                            text_y = y
                            print(f"    -> Détecté hauteur={extrude_height} - Pas d'acrotère")
                            break
                        
                        if "etg" in txt.lower():
                            has_etg = True
                            zone_name = zone_name_clean if zone_name_clean else "IMM"
                            zone_color = col
                            text_x = x
                            text_y = y
                            print(f"    -> Détecté 'etg' - Acrotère activé")
                            break
                        
                        zone_name = zone_name_clean
                        zone_color = col
                        text_x = x
                        text_y = y
                        break

        if not zone_name:
            continue

        zone_key = f"{zone_name}_{zone_index}"
        if zone_key in processed_zones:
            continue
        
        processed_zones.add(zone_key)
        print(f"\n=== معالجة المنطقة {zone_name} (المؤشر {zone_index}) ===")
        print(f"  has_etg: {has_etg}")
        print(f"  extrude_height: {extrude_height}")

        current_zone_red = []
        for red_line in red_elements:
            if poly.exterior.buffer(tolerance).intersects(red_line):
                current_zone_red.append(red_line)
        
        red_encorbellement_polygons_coords = create_encorbellement_from_red_lines(
            current_zone_red, poly, 1.2, tolerance
        )
        
        side_lines, gap_fillers = close_all_gaps_between_red_elements(
            current_zone_red, poly, 1.2, gap_tolerance=0.5
        )
        
        print("\n=== معالجة RED_BLOCK_2D ===")
        
        gap_fillers_lines = [fill_line for fill_line in gap_fillers]
        
        merged_red_block = process_and_merge_polylines(
            gap_fillers_lines,
            red_encorbellement_polygons_coords,
            distance_threshold=1.22
        )
        
        print("\n=== معالجة ACROTERION_2D ===")
        
        merged_acroterion = []
        
        if has_etg and zone_name not in ["trottoir", "voie"]:
            temp_acro_outer, temp_acro_inner = create_acroterion_wall_inward(
                coords,
                red_encorbellement_polygons_coords,
                wall_thickness=0.2,
                height=1.2,
                tolerance=tolerance
            )
            
            acroterion_coords_list = []
            if temp_acro_outer:
                acroterion_coords_list.append(temp_acro_outer)
            
            merged_acroterion = process_and_merge_polylines(
                gap_fillers_lines,
                acroterion_coords_list,
                distance_threshold=1.22
            )
        else:
            print(f"  === PAS de création d'acrotérion car has_etg={has_etg} et zone_name={zone_name} ===")
        
        # ============================================
        # رسم 2D
        # ============================================
        
        if "RED_BLOCK_2D" not in doc2d.layers:
            doc2d.layers.new(name="RED_BLOCK_2D", dxfattribs={"color": 1})
        
        for r_poly_coords in merged_red_block:
            try:
                msp2d.add_lwpolyline(r_poly_coords, close=True, dxfattribs={"layer": "RED_BLOCK_2D", "color": 1})
                print(f"  تم رسم RED_BLOCK_2D بعد المعالجة")
            except:
                try:
                    msp2d.add_lwpolyline(r_poly_coords, close=False, dxfattribs={"layer": "RED_BLOCK_2D", "color": 1})
                except:
                    pass
        
        if merged_acroterion:
            if "ACROTERION_2D" not in doc2d.layers:
                doc2d.layers.new(name="ACROTERION_2D", dxfattribs={"color": zone_color})
            
            for acro_coords in merged_acroterion:
                try:
                    msp2d.add_lwpolyline(acro_coords, close=True, dxfattribs={"layer": "ACROTERION_2D", "color": zone_color})
                    print(f"  تم رسم ACROTERION_2D بعد المعالجة")
                except:
                    try:
                        msp2d.add_lwpolyline(acro_coords, close=False, dxfattribs={"layer": "ACROTERION_2D", "color": zone_color})
                    except:
                        pass
        
        if "SIDE_LINES_2D" not in doc2d.layers:
            doc2d.layers.new(name="SIDE_LINES_2D", dxfattribs={"color": 3})
        
        for side_type, side_line in side_lines:
            try:
                side_coords = list(side_line.coords)
                msp2d.add_lwpolyline(side_coords, close=False, dxfattribs={"layer": "SIDE_LINES_2D", "color": 3})
            except:
                pass
        
        if gap_fillers:
            if "GAPS_FILL_2D" not in doc2d.layers:
                doc2d.layers.new(name="GAPS_FILL_2D", dxfattribs={"color": 4})
            
            for fill_line in gap_fillers:
                try:
                    fill_coords = list(fill_line.coords)
                    msp2d.add_lwpolyline(fill_coords, close=False, dxfattribs={"layer": "GAPS_FILL_2D", "color": 4})
                except:
                    pass
        
        if zone_name not in doc2d.layers:
            doc2d.layers.new(name=zone_name, dxfattribs={"color": zone_color})
        
        try:
            msp2d.add_lwpolyline(coords, close=True, dxfattribs={"layer": zone_name, "color": zone_color})
        except:
            pass
        
        area = poly.area
        area_text = f"{area:.2f} m\u00B2"
        text_height = 0.21
        offset = 0.15
        if text_x and text_y:
            msp2d.add_text(area_text, dxfattribs={"style": "ArialStyle", "height": text_height, "layer": zone_name, "color": zone_color, "insert": (text_x, text_y - text_height - offset)})
        
        # ============================================
        # إنشاء 3D
        # ============================================
        
        if zone_name == "trottoir":
            print(f"\n=== Création spéciale pour Trottoir (hauteur=0.15) ===")
            if "trottoir_B" not in doc3d.layers:
                doc3d.layers.new(name="trottoir_B", dxfattribs={"color": 41})
            create_sidewalk_B_3d(doc3d, msp3d, coords, 0, zone_index)
            
        elif zone_name == "voie":
            print(f"\n=== Création spéciale pour voie (hauteur=0.05) ===")
            if "voie_B" not in doc3d.layers:
                doc3d.layers.new(name="voie_B", dxfattribs={"color": 251})
            create_road_B_3d(doc3d, msp3d, coords, 0, zone_index)
        
        else:
            def add_3d_elements_for_level(current_coords, current_height, current_layer, current_color, current_index, current_base_z, add_red_blocks=False):
                # Utilisation de create_3d_stack pour les niveaux (sans faces top/bottom)
                create_3d_stack(doc3d, msp3d, current_coords, current_height, current_layer, current_color, current_index, current_base_z)
                # Ajout des faces top et bottom dans le calque "topdown"
                topdown_niveau(doc3d, msp3d, current_coords, current_height, current_layer, current_color, current_index, current_base_z)
                
                if add_red_blocks:
                    process_red_blocks_A(doc3d, msp3d, merged_red_block, current_index, current_base_z, current_height)
                    process_red_blocks_B(doc3d, msp3d, merged_red_block, current_zone_red, poly, current_index, current_base_z, current_height, 1.2)
            
            if block_heights:
                add_3d_elements_for_level(coords, 4.5, zone_name, zone_color, zone_index, 0, add_red_blocks=False)
                last_z = 0
                last_h = 4.5
                
                for z, h in block_heights:
                    add_3d_elements_for_level(coords, h, zone_name, zone_color, zone_index, z, add_red_blocks=True)
                    last_z, last_h = z, h
                
                roof_base_z = last_z + last_h
                
                if merged_acroterion:
                    print(f"  === Création de l'acrotère pour {zone_name} (R+{floors}) à la hauteur {roof_base_z} ===")
                    for acro_coords in merged_acroterion:
                        outer_coords_ac, inner_coords_ac = create_acroterion_wall_inward(
                            acro_coords,
                            merged_red_block,
                            wall_thickness=0.2,
                            height=1.2,
                            tolerance=tolerance
                        )
                        
                        if outer_coords_ac and inner_coords_ac and len(outer_coords_ac) > 2 and len(inner_coords_ac) > 2:
                            if "ACROTERION" not in doc3d.layers:
                                doc3d.layers.new(name="ACROTERION", dxfattribs={"color": zone_color})
                            
                            block_name = f"ACROTERION_{zone_index}_{roof_base_z}"
                            create_hollow_wall(
                                doc3d,
                                msp3d,
                                outer_coords_ac,
                                inner_coords_ac,
                                1.2,
                                "ACROTERION",
                                zone_color,
                                block_name,
                                roof_base_z,
                                merged_red_block
                            )
                            print(f"    -> Acrotère créé avec succès pour {zone_name}")
            
            elif extrude_height > 0:
                add_3d_elements_for_level(coords, extrude_height, zone_name, zone_color, zone_index, 0, add_red_blocks=False)
                
                for f in range(floors):
                    z = (f + 1) * extrude_height
                    add_3d_elements_for_level(coords, extrude_height, zone_name, zone_color, zone_index, z, add_red_blocks=True)
                
                roof_z = (floors + 1) * extrude_height
                
                if merged_acroterion:
                    print(f"  === Création de l'acrotère pour {zone_name} à la hauteur {roof_z} ===")
                    for acro_coords in merged_acroterion:
                        outer_coords_ac, inner_coords_ac = create_acroterion_wall_inward(
                            acro_coords,
                            merged_red_block,
                            wall_thickness=0.2,
                            height=1.2,
                            tolerance=tolerance
                        )
                        
                        if outer_coords_ac and inner_coords_ac and len(outer_coords_ac) > 2 and len(inner_coords_ac) > 2:
                            if "ACROTERION" not in doc3d.layers:
                                doc3d.layers.new(name="ACROTERION", dxfattribs={"color": zone_color})
                            
                            block_name = f"ACROTERION_{zone_index}_{roof_z}"
                            create_hollow_wall(
                                doc3d,
                                msp3d,
                                outer_coords_ac,
                                inner_coords_ac,
                                1.2,
                                "ACROTERION",
                                zone_color,
                                block_name,
                                roof_z,
                                merged_red_block
                            )
                            print(f"    -> Acrotère créé avec succès pour {zone_name}")
    
    file2d = os.path.join(UPLOAD_FOLDER, "zones_2D.dxf")
    file3d = os.path.join(UPLOAD_FOLDER, "zones_3D.dxf")
    doc2d.saveas(file2d)
    doc3d.saveas(file3d)

    # ============================================================
   
    return jsonify({"file2d": "/download/zones_2D.dxf", "file3d": "/download/zones_3D.dxf"})

@bp2.route("/download/<filename>")
def download(filename):
    path = os.path.join(UPLOAD_FOLDER, filename)
    return send_file(path, as_attachment=True)

if __name__ == "__main__":
    from flask import Flask
    test_app = Flask(__name__)
    test_app.register_blueprint(bp2)
    test_app.run(debug=True)