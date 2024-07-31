import cv2
import numpy as np
import matplotlib.pyplot as plt
import io
from PIL import Image


def get_min_rect_len(seg_mask):
    """计算掩码的最小外接矩形的长度，这里指的 `width`"""
    # 找到掩码中所有像素点为 True 的坐标
    points = np.column_stack(np.where(seg_mask)).astype(np.int32)
    # 计算最小外接矩形 ((center_x, center_y), (width, height), angle)
    (_, (width, height), _) = cv2.minAreaRect(points)
    ratio = width / height if height != 0 else 0
    return width, height, ratio


def draw_masks_on_image(img_shape, anns, x_offset=0, y_offset=0):
    mask = np.zeros(img_shape, dtype=np.uint8)
    if anns is None:
        return mask
    if (not isinstance(anns, list)) and (not isinstance(anns, np.ndarray)):
        anns = [anns]
    if len(anns) == 0:
        return mask
    
    for ann in anns:
        if ann is None:
            continue
        
        color_mask = np.random.randint(0, 255, (3,), dtype=int)
        if len(ann.shape) == 2:
            y_indices, x_indices = np.nonzero(ann)
            y_indices = y_indices + y_offset
            x_indices = x_indices + x_offset
            
            mask[y_indices, x_indices] = color_mask
    
    return mask


def create_roi_mask(frame_shape, x1, y1, x2, y2, label):
    """
    在指定的 ROI 区域内绘制一个蓝色框，并在框上方显示标签内容。

    参数：
    frame_shape (tuple): 原始图像的形状。
    x1, y1, x2, y2 (int): ROI区域的边界框坐标。
    label (str): 要显示的标签内容。

    返回：
    np.ndarray: 带有蓝色框和标签的mask数组。
    """
    height, width, _ = frame_shape
    mask = np.zeros((height, width, 3), dtype=np.uint8)
    
    color = (0, 0, 255)
    thickness = 2
    cv2.rectangle(mask, (x1, y1), (x2, y2), color, thickness)
    
    # 在框上方显示标签内容
    font = cv2.FONT_HERSHEY_COMPLEX
    font_scale = 1
    font_thickness = 2
    text_size = cv2.getTextSize(label, font, font_scale, font_thickness)[0]
    text_x = x1
    text_y = y1 - 10 if y1 - 10 > 10 else y1 + 10 + text_size[1]
    if label:
        cv2.putText(mask, label, (text_x, text_y), font, font_scale, color, font_thickness, cv2.LINE_AA)
    
    return mask


def filter_masks(masks, topn=1):
    """
    过滤掉不符合特定条件的遮罩（masks）。

    参数：
    masks (list): 包含遮罩信息的字典列表，每个字典包含 'bbox' 键和相应的边界框信息。

    返回：
    list: 过滤后的遮罩列表。
    """
    if len(masks) == 0:
        return None
    crop_box = masks[0]['crop_box']
    total_area = (crop_box[2] - crop_box[0]) * (crop_box[3] - crop_box[1])
    scores = [0] * len(masks)
    for idx, mask in enumerate(masks):
        bbox = mask['bbox']
        area = mask['area']
        # 最小外接矩形长宽比越大，得分越高
        width, height, ratio = get_min_rect_len(mask)
        scores[idx] += ratio
        # 物体中心点距离区域中心点越近，得分越高
        distance = np.sqrt(((bbox[0] + bbox[2] / 2) - ((crop_box[0] + crop_box[2]) / 2))**2
                           + ((bbox[1] + bbox[3] / 2) - ((crop_box[1] + crop_box[3]) / 2))**2)
        scores[idx] += 2 * 1000 / distance
        # 面积较大或较小的的分数低
        scores[idx] += 5 - area / total_area * 100
        if area < 300 or area > 3000:
            scores[idx] -= 100
        # 物体左侧在区域左侧且物体右侧在区域右侧分数高
        mid = (crop_box[0] + crop_box[2]) / 2
        if (bbox[0] < mid) and (bbox[0] + bbox[2] > mid):
            scores[idx] += 30
    # 获取分数最高的topn个掩码的索引
    topn_indices = np.argsort(scores)[-topn:]
    
    # 返回分数最高的topn个掩码
    return [masks[i] for i in topn_indices][::-1]  # 按照分数从高到低返回
