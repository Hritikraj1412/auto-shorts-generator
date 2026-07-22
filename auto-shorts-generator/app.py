import glob 
import os
import cv2
import numpy as np
import subprocess
import imageio_ffmpeg
import whisper
import wave  # <-- NEW: Inbuilt library to read audio directly
from flask import Flask, request, render_template, jsonify

app = Flask(__name__)

UPLOAD_DIR = 'uploads'
CLIPS_DIR = os.path.join('static', 'clips')
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CLIPS_DIR, exist_ok=True)
app.config['UPLOAD_DIR'] = UPLOAD_DIR

print("Loading AI Whisper Model...")
model = whisper.load_model("tiny")
print("Model Loaded!")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/process_video', methods=['POST'])
def process_video():

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

    # 1. FIND HIGHLIGHTS FAST
    cap = cv2.VideoCapture(filepath)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0 or np.isnan(fps): fps = 30
        
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = int(total_frames / fps)

    motion_scores = []
    prev_frame = None
    
    # Analyze video by jumping 2 seconds for speed
    for sec in range(0, duration_sec, 2):
        cap.set(cv2.CAP_PROP_POS_FRAMES, sec * fps)
        ret, frame = cap.read()
        if not ret: break
                
        small_frame = cv2.resize(frame, (320, 240))
        gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

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
        
        temp_clip = os.path.join(CLIPS_DIR, f"temp_raw_{i}.mp4")
        audio_filepath = os.path.join(CLIPS_DIR, f"temp_audio_{i}.wav")
        final_clip = os.path.join(CLIPS_DIR, f"highlight_{i+1}.mp4")
        temp_no_audio_vid = os.path.join(CLIPS_DIR, f"temp_burned_{i}.avi")
        
        try:
            # A. Cut Raw Clip instantly using local FFmpeg
            subprocess.run([ffmpeg_exe, '-y', '-ss', str(start_sec), '-i', filepath, '-t', str(clip_duration), '-c:v', 'copy', '-c:a', 'copy', temp_clip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            
            # B. Extract Audio in 16kHz mono WAV format
            subprocess.run([ffmpeg_exe, '-y', '-i', temp_clip, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', audio_filepath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            
            # C. THE FIX: Bypass Whisper's file reading and feed raw math array directly
            with wave.open(audio_filepath, 'rb') as wf:
                frames = wf.readframes(wf.getnframes())
                audio_array = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            
            # Transcribe directly from memory array
            result = model.transcribe(audio_array)
            segments = result.get("segments", [])

            # D. HARD-BURN SUBTITLES INTO FRAMES WITH OPENCV
            cap_clip = cv2.VideoCapture(temp_clip)
            w = int(cap_clip.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap_clip.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            out = cv2.VideoWriter(temp_no_audio_vid, fourcc, fps, (w, h))

            frame_idx = 0
            while True:
                ret, frame = cap_clip.read()
                if not ret: break

                current_time = frame_idx / fps
                current_text = ""
                
                # Check which word/sentence matches exact current second
                for seg in segments:
                    if seg['start'] <= current_time <= seg['end']:
                        current_text = seg['text'].strip().upper()
                        break
                
                # Paint Text on video permanently
                if current_text:
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    font_scale = max(1.0, w / 800.0)
                    thickness = int(max(2, w / 350.0))
                    (text_w, text_h), _ = cv2.getTextSize(current_text, font, font_scale, thickness)
                    
                    x = int((w - text_w) / 2)
                    y = int(h - (h * 0.15)) # 15% from bottom
                    
                    # Draw Black Outline for crazy look
                    cv2.putText(frame, current_text, (x, y), font, font_scale, (0, 0, 0), thickness * 3, cv2.LINE_AA)
                    # Draw Yellow Text Inside (BGR format: 0, 255, 255)
                    cv2.putText(frame, current_text, (x, y), font, font_scale, (0, 255, 255), thickness, cv2.LINE_AA)

                out.write(frame)
                frame_idx += 1
                
            cap_clip.release()
            out.release()

            # E. Merge Burned Video + Original Audio perfectly
            subprocess.run([
                ffmpeg_exe, '-y', '-i', temp_no_audio_vid, '-i', audio_filepath,
                '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', final_clip
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            
            # Cleanup temp files
            for f in [temp_clip, audio_filepath, temp_no_audio_vid]:
                if os.path.exists(f): os.remove(f)

            highlights.append({
                "title": f"Viral Clip #{i+1}",
                "download_url": f"/{final_clip}"
            })
        except Exception as e:
            print(f"Error on clip {i}: {e}")
            continue

    return jsonify({"status": "success", "clips": highlights})

if __name__ == '__main__':
    app.run(debug=True)