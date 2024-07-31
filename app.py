import gradio as gr
import cv2
import tempfile
import torch

from ultralytics import YOLO
from utils.config import get_config
from utils.needle_clasify import load_efficient_net, predict_and_find_start_inserted
from utils.mask_tools import draw_masks_on_image, create_roi_mask, get_min_rect_len

CONFIG = get_config()

INIT_SHAFT_LEN = 20  # 针梗的实际长度，单位为毫米
MOVE_THRESHOLD = 2  # 针梗移动的阈值，单位为毫米
CONFIRMATION_FRAMES = 5  # 连续几帧确认像素比例和插入状态
OUT_EXPAND = 50  # 输出图像感兴趣区域的扩展像素数


def yolo_inference(image, video,
                   yolo_model_id,
                   classify_model_id,
                   image_size,
                   yolo_conf_threshold,
                   judge_wnd):
    model = YOLO(f'{yolo_model_id}')
    if image:
        results = model.predict(source=image, imgsz=image_size, conf=yolo_conf_threshold)
        annotated_image = results[0].plot()
        return annotated_image, None
    else:
        video_path = tempfile.mktemp(suffix=".mp4")
        
        with open(video_path, "wb") as f:
            with open(video, "rb") as g:
                f.write(g.read())
        
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        output_video_path = tempfile.mktemp(suffix=".mp4")
        out = cv2.VideoWriter(output_video_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (frame_width, frame_height))
        
        yolo_pred_xyxy = []  # yolo 预测的目标位置信息
        anns = []  # 实例分割标注数组
        last_box = None  # 上一帧的目标位置信息
        frames = []  # 帧列表
        yolo_batch_size = 4
        pixel_len_arr = []  # 视频中针梗的长度，以像素为单位
        inserted = False  # 是否插入皮肤（只判断初始固定距离）
        insert_start_frame, insert_spec_end_frame = None, None  # 记录插入皮肤的开始和指定结束所在帧
        spec_insert_speed = None  # 插入皮肤指定长度的速度
        speed_clac_compute = False  # 是否开始计算插入皮肤的速度
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(frame)
            
            results = model.predict(source=frame, imgsz=image_size, conf=yolo_conf_threshold)
            pred_boxes = results[0].boxes
            height, width, _ = frame.shape
            
            if len(pred_boxes.cls) > 0:
                # 若检测到多个物体，取置信度最大的
                best_conf_idx = torch.argmax(pred_boxes.conf)
                xyxy_box = pred_boxes.xyxy[best_conf_idx].squeeze().cpu()
                xyxy_box = list(map(int, xyxy_box))
                last_box = xyxy_box
                seg_mask = results[0].masks.cpu().numpy().data[best_conf_idx]
                print(seg_mask.shape)
                anns.append(seg_mask)
            else:
                if last_box is None:
                    xyxy_box = 0, 0, width, height
                else:
                    xyxy_box = last_box
                anns.append(None)
            
            yolo_pred_xyxy.append(xyxy_box)
        
        cls_model = load_efficient_net(name=classify_model_id)
        class_list, prob_list, insert_start_frame = predict_and_find_start_inserted(
            cls_model,
            frames=frames,
            boxes_list=yolo_pred_xyxy,
            judge_wnd=judge_wnd,
            batch_size=yolo_batch_size)
        
        last_xyxy = None
        last_rect_len = None
        for idx, (frame, ann, xyxy, cls, prob) in enumerate(zip(frames, anns, yolo_pred_xyxy,
                                                                class_list, prob_list)):
            height, width, _ = frame.shape
            
            if inserted:
                x1, y1, x2, y2 = last_xyxy
            else:
                x1, y1, x2, y2 = xyxy
                x1 = max(0, x1 - OUT_EXPAND)
                y1 = max(0, y1 - OUT_EXPAND)
                x2 = min(width, x2 + OUT_EXPAND)
                y2 = min(height, y2 + OUT_EXPAND)
                last_xyxy = x1, y1, x2, y2
            
            if ann is not None:
                rect_len = get_min_rect_len(ann)[0]
                last_rect_len = rect_len
            else:
                rect_len = last_rect_len
            
            if cls == 0 and not inserted and ann is not None:
                pixel_len_arr.append(rect_len)
                if len(pixel_len_arr) > CONFIRMATION_FRAMES:
                    pixel_len_arr.pop(0)
            # if cls == 1 and len(pixel_len_arr) == 0:
            #     # 第一帧就检测到插入皮肤的情况
            #     pixel_len_arr.append(shaft_pixel_len)
            actual_len = INIT_SHAFT_LEN if cls == 0 else (
                    INIT_SHAFT_LEN * rect_len / (sum(pixel_len_arr) / len(pixel_len_arr)))
            
            # 判断是否开始插入皮肤
            if idx == insert_start_frame:
                inserted = True
            
            # 判断是否插入皮肤达到指定长度
            if cls == 1 and inserted and actual_len <= INIT_SHAFT_LEN - MOVE_THRESHOLD:
                inserted = False
                speed_clac_compute = True
                insert_spec_end_frame = cap.get(cv2.CAP_PROP_POS_FRAMES)
                interval_time = (insert_spec_end_frame - insert_start_frame) / fps
                spec_insert_speed = 1000 * MOVE_THRESHOLD / interval_time
            
            if speed_clac_compute:
                label = f"{cls} {prob:.2f} {spec_insert_speed:.2f}mm/s"
            else:
                label = f"{cls} {prob:.2f} {actual_len:.2f} {rect_len:.2f}"
            
            mask = draw_masks_on_image(frame.shape, ann)
            roi_mask = create_roi_mask(frame.shape, x1, y1, x2, y2, label)
            combined_frame = cv2.addWeighted(frame, 1, mask, 1, 0)
            combined_frame = cv2.addWeighted(combined_frame, 1, roi_mask, 1, 0)
            out.write(combined_frame)
        
        cap.release()
        out.release()
        print("Start: ", insert_start_frame, " End: ", insert_spec_end_frame)
        
        return None, output_video_path


def app():
    with gr.Blocks():
        with gr.Row():
            with gr.Column():
                image = gr.Image(type="pil", label="Image", visible=False)
                video = gr.Video(label="Video", visible=True)
                input_type = gr.Radio(
                    choices=["Image", "Video"],
                    value="Video",
                    label="Input Type",
                )
                yolo_model_id = gr.Dropdown(
                    label="YOLO Model",
                    choices=[
                        "seg/best.pt"
                    ],
                    value="seg/best.pt",
                )
                classify_model_id = gr.Dropdown(
                    label="Classify Model",
                    choices=[
                        "EfficientNet/EfficientNet_23.pkl",
                    ],
                    value="EfficientNet/EfficientNet_23.pkl"
                )
                image_size = gr.Slider(
                    label="Image Size",
                    minimum=320,
                    maximum=1280,
                    step=32,
                    value=640,
                )
                yolo_conf_threshold = gr.Slider(
                    label="Confidence Threshold",
                    minimum=0.0,
                    maximum=1.0,
                    step=0.05,
                    value=0.35,
                )
                judge_wnd = gr.Slider(
                    label="Window Size for Judging Insert-starting Frame",
                    minimum=10,
                    maximum=40,
                    step=5,
                    value=20,
                )
                yolov10_infer = gr.Button(value="Detect Objects")
            
            with gr.Column():
                output_image = gr.Image(type="numpy", label="Annotated Image", visible=False)
                output_video = gr.Video(label="Annotated Video", visible=True)
        
        def update_visibility(input_type):
            image = gr.update(visible=True) if input_type == "Image" else gr.update(visible=False)
            video = gr.update(visible=False) if input_type == "Image" else gr.update(visible=True)
            output_image = gr.update(visible=True) if input_type == "Image" else gr.update(visible=False)
            output_video = gr.update(visible=False) if input_type == "Image" else gr.update(visible=True)
            
            return image, video, output_image, output_video
        
        input_type.change(
            fn=update_visibility,
            inputs=[input_type],
            outputs=[image, video, output_image, output_video],
        )
        
        def run_inference(image, video,
                          yolo_model_id,
                          classify_model_id,
                          image_size,
                          yolo_conf_threshold,
                          judge_wnd,
                          input_type):
            if input_type == "Image":
                return yolo_inference(image, None,
                                      yolo_model_id,
                                      classify_model_id,
                                      image_size,
                                      yolo_conf_threshold=yolo_conf_threshold,
                                      judge_wnd=judge_wnd)
            else:
                return yolo_inference(None, video,
                                      yolo_model_id,
                                      classify_model_id,
                                      image_size,
                                      yolo_conf_threshold=yolo_conf_threshold,
                                      judge_wnd=judge_wnd)
        
        yolov10_infer.click(
            fn=run_inference,
            inputs=[image, video,
                    yolo_model_id,
                    classify_model_id,
                    image_size,
                    yolo_conf_threshold,
                    judge_wnd,
                    input_type],
            outputs=[output_image, output_video],
        )


gradio_app = gr.Blocks()
with gradio_app:
    gr.HTML(
        """
    <h1 style='text-align: center'>
    Puncture Detection
    </h1>
    """)
    # gr.HTML(
    #     """
    #     <h3 style='text-align: center'>
    #     <a href='https://arxiv.org/abs/2405.14458' target='_blank'>arXiv</a> | <a href='https://github.com/THU-MIG/yolov10' target='_blank'>github</a>
    #     </h3>
    #     """)
    with gr.Row():
        with gr.Column():
            app()
if __name__ == '__main__':
    gradio_app.launch()
