import os
import cv2
import numpy as np
import subprocess
import imageio_ffmpeg
import glob
from flask import Flask, request, render_template, jsonify

app = Flask(__name__)

UPLOAD_DIR = 'uploads'
CLIPS_DIR = os.path.join('static', 'clips')
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLIPS_DIR, exist_ok=True)
app.config['UPLOAD_DIR'] = UPLOAD_DIR

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/process_video', methods=['POST'])
def process_video():
    # Auto delete old files to save free server space
    for f in glob.glob(os.path.join(app.config['UPLOAD_DIR'], '*')) + glob.glob(os.path.join(CLIPS_DIR, '*')):
        try: os.remove(f)
        except: pass

    if 'video' not in request.files:
        return jsonify({"error": "No video file found"}), 400
    
    vid_file = request.files['video']
    if vid_file.filename == '':
        return jsonify({"error": "Empty filename"}), 400

    filepath = os.path.join(app.config['UPLOAD_DIR'], vid_file.filename)
    vid_file.save(filepath)

    # 1. LIGHTWEIGHT HYPER-FAST ANALYSIS
    cap = cv2.VideoCapture(filepath)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0 or np.isnan(fps): fps = 30
        
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = int(total_frames / fps)

    motion_scores = []
    prev_frame = None
    
    for sec in range(0, duration_sec, 3): # Check every 3 seconds for low RAM usage
        cap.set(cv2.CAP_PROP_POS_FRAMES, sec * fps)
        ret, frame = cap.read()
        if not ret: break
                
        small_frame = cv2.resize(frame, (160, 120)) # Tiny resolution to save RAM
        gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)

        if prev_frame is not None:
            diff = cv2.absdiff(prev_frame, gray)
            motion_scores.append((sec, np.sum(diff)))
        prev_frame = gray

    cap.release()

    if not motion_scores:
        return jsonify({"error": "Could not analyze the video properly."}), 400

    motion_scores.sort(key=lambda x: x[1], reverse=True)
    top_moments_count = min(5, len(motion_scores))
    highlights = []
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    
    for i in range(top_moments_count):
        peak_sec = motion_scores[i][0]
        start_sec = max(0, peak_sec - 5)
        end_sec = min(duration_sec, peak_sec + 10)
        clip_duration = end_sec - start_sec
        
        final_clip = os.path.join(CLIPS_DIR, f"highlight_{i+1}.mp4")
        
        # INSTANT STREAM COPY (Zero memory overhead)
        cmd_cut = [
            ffmpeg_exe, '-y', '-ss', str(start_sec), '-i', filepath, 
            '-t', str(clip_duration), '-c:v', 'copy', '-c:a', 'copy', final_clip
        ]
        
        try:
            subprocess.run(cmd_cut, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            highlights.append({
                "title": f"Viral Clip #{i+1}",
                "download_url": f"/{final_clip}"
            })
        except Exception as e:
            continue

    return jsonify({"status": "success", "clips": highlights})

if __name__ == '__main__':
    app.run(debug=True)