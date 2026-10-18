#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Medical Dashboard Server for Ma Hanh's Medical Records & Timeline
Author: Antigravity Healthcare & Data Specialist
"""

import os
import json
import re
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import fitz  # PyMuPDF
import pdfplumber

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
DATA_FILE = os.path.join(BASE_DIR, "records_data.json")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
STATIC_FOLDER = os.path.join(BASE_DIR, "static")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_FOLDER, static_url_path="")
CORS(app)

import shutil

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"patient": {}, "records": []}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    static_data_file = os.path.join(STATIC_FOLDER, "records_data.json")
    try:
        with open(static_data_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving to static records_data.json: {e}")

@app.route("/")
def index():
    return send_from_directory(STATIC_FOLDER, "index.html")

@app.route("/api/data", methods=["GET"])
def get_data():
    data = load_data()
    # Sort records chronologically
    data["records"] = sorted(data.get("records", []), key=lambda x: x.get("date", ""))
    return jsonify(data)

@app.route("/api/records/save", methods=["POST"])
def save_record():
    payload = request.json
    if not payload:
        return jsonify({"error": "No data provided"}), 400
    
    data = load_data()
    records = data.get("records", [])
    
    rec_id = payload.get("id")
    if not rec_id:
        rec_id = f"rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        payload["id"] = rec_id
    
    # Check if updating existing
    updated = False
    for idx, r in enumerate(records):
        if r.get("id") == rec_id:
            records[idx] = payload
            updated = True
            break
    
    if not updated:
        records.append(payload)
        
    records = sorted(records, key=lambda x: x.get("date", ""))
    data["records"] = records
    save_data(data)
    
    return jsonify({"success": True, "record": payload, "count": len(records)})

@app.route("/api/records/<rec_id>", methods=["DELETE"])
def delete_record(rec_id):
    data = load_data()
    records = [r for r in data.get("records", []) if r.get("id") != rec_id]
    data["records"] = records
    save_data(data)
    return jsonify({"success": True, "count": len(records)})

@app.route("/api/sync-gdrive", methods=["GET", "POST"])
def sync_gdrive():
    """
    Scans Google Drive directory (PARENT_DIR), copies new files to static/pdfs/,
    extracts metadata and auto-creates medical records for unindexed reports.
    """
    import urllib.parse
    try:
        data = load_data()
        records = data.get("records", [])
        
        # Build set of existing referenced filenames (normalized NFC)
        import unicodedata
        known_files = set()
        for r in records:
            if r.get("pdf_file"):
                known_files.add(unicodedata.normalize('NFC', r.get("pdf_file")))
            for doc in r.get("documents", []):
                if doc.get("pdf_file"):
                    known_files.add(unicodedata.normalize('NFC', doc.get("pdf_file")))
                    
        valid_exts = ('.pdf', '.jpg', '.jpeg', '.png', '.webp')
        parent_files = [f for f in os.listdir(PARENT_DIR) if os.path.isfile(os.path.join(PARENT_DIR, f)) and f.lower().endswith(valid_exts) and not f.startswith('.')]
        
        target_pdfs_dir = os.path.join(STATIC_FOLDER, "pdfs")
        os.makedirs(target_pdfs_dir, exist_ok=True)
        
        copied_files = []
        new_records_added = []
        
        for fname in parent_files:
            fname_nfc = unicodedata.normalize('NFC', fname)
            src_path = os.path.join(PARENT_DIR, fname)
            dst_path = os.path.join(target_pdfs_dir, fname)
            
            # Ensure file is copied to static/pdfs/
            if not os.path.exists(dst_path) or os.path.getsize(src_path) != os.path.getsize(dst_path):
                shutil.copy2(src_path, dst_path)
                copied_files.append(fname)
                
            # If not in records, parse and create new record
            if fname_nfc not in known_files:
                full_text = ""
                if fname.lower().endswith('.pdf'):
                    try:
                        doc = fitz.open(src_path)
                        for page in doc:
                            full_text += page.get_text() + "\n"
                    except Exception as e:
                        print(f"Error reading PDF {fname}: {e}")
                    
                    if len(full_text.strip()) < 50:
                        try:
                            with pdfplumber.open(src_path) as pdf:
                                for page in pdf.pages:
                                    t = page.extract_text()
                                    if t: full_text += t + "\n"
                        except Exception as e:
                            print(f"pdfplumber error: {e}")
                            
                draft = parse_extracted_text(full_text, fname)
                clean_url = f"/pdfs/{urllib.parse.quote(fname)}"
                draft["pdf_url"] = clean_url
                draft["documents"] = [
                    {
                        "name": draft.get("title", fname),
                        "category": draft.get("type", "imaging"),
                        "icon": "📄" if draft.get("type") == "imaging" else "🧪",
                        "pdf_file": fname,
                        "pdf_url": clean_url,
                        "badge": "Mới nạp từ Drive"
                    }
                ]
                
                # Check if there is already a record for this date & facility
                rec_date = draft.get("date")
                existing_same_day = [r for r in records if r.get("date") == rec_date and r.get("facility") == draft.get("facility")]
                if existing_same_day:
                    existing_rec = existing_same_day[0]
                    if "documents" not in existing_rec:
                        existing_rec["documents"] = []
                    existing_rec["documents"].append(draft["documents"][0])
                    new_records_added.append(f"{fname} (Gộp vào ngày {rec_date})")
                else:
                    records.append(draft)
                    new_records_added.append(f"{fname} (Đợt khám mới {rec_date})")
                
                known_files.add(fname_nfc)
                
        # Sort records chronologically
        records = sorted(records, key=lambda x: x.get("date", ""))
        data["records"] = records
        data["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        save_data(data)
        
        # Auto-trigger Vercel production deploy in background if new records were added
        if new_records_added:
            def _async_deploy():
                try:
                    import subprocess
                    print(f"Auto-deploying {len(new_records_added)} new records to Vercel production...")
                    subprocess.run(["npx", "vercel", "--prod", "--yes"], cwd=BASE_DIR, capture_output=True, text=True)
                    print("Auto-deploy to Vercel finished successfully.")
                except Exception as ex:
                    print(f"Auto-deploy to Vercel error: {ex}")
            import threading
            threading.Thread(target=_async_deploy, daemon=True).start()
            
        return jsonify({
            "success": True,
            "message": "Đồng bộ Google Drive thành công",
            "last_sync": data["last_sync"],
            "total_files_in_drive": len(parent_files),
            "copied_files_count": len(copied_files),
            "new_records_count": len(new_records_added),
            "new_records": new_records_added,
            "total_records": len(records),
            "records": records
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/pdf/<path:filename>")
def get_pdf(filename):
    import urllib.parse
    filename = urllib.parse.unquote(filename)
    # Check parent dir, static/pdfs, then uploads
    parent_path = os.path.join(PARENT_DIR, filename)
    if os.path.exists(parent_path):
        return send_file(parent_path)
    static_pdf = os.path.join(STATIC_FOLDER, "pdfs", filename)
    if os.path.exists(static_pdf):
        return send_file(static_pdf)
    upload_path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(upload_path):
        return send_file(upload_path)
    return jsonify({"error": "File not found"}), 404

def parse_extracted_text(text, filename):
    """
    Intelligent medical regex parser for Vietnam clinical reports
    (Medic Hoa Hao, Cho Ray, BV Thong Nhat, Viet Healthcare, etc.)
    """
    draft = {
        "id": f"rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "facility": "Cơ sở Y tế",
        "doctor": "",
        "type": "lab",
        "title": "Báo Cáo Y Tế Mới",
        "summary": "",
        "details": {},
        "lab_results": {},
        "metrics": {},
        "raw_text": text[:3000],
        "pdf_file": filename
    }

    # Detect Date
    date_match = re.search(r"(\d{2})[/-](\d{2})[/-](202\d)", text)
    if date_match:
        day, month, year = date_match.groups()
        draft["date"] = f"{year}-{month}-{day}"
    else:
        # Check filename for YYYYMMDD
        fn_match = re.search(r"(202\d)(\d{2})(\d{2})", filename)
        if fn_match:
            year, month, day = fn_match.groups()
            draft["date"] = f"{year}-{month}-{day}"

    # Detect Facility
    if "Medic" in text or "HÒA HẢO" in text.upper():
        draft["facility"] = "Trung tâm Y khoa Medic Hòa Hảo"
    elif "THỐNG NHẤT" in text.upper():
        draft["facility"] = "Bệnh viện Thống Nhất"
    elif "VIỆT HEALTHCARE" in text.upper():
        draft["facility"] = "Phòng khám Đa Khoa Quốc Tế Việt Healthcare"
    elif "ĐẠI PHƯỚC" in text.upper():
        draft["facility"] = "Phòng khám Đa Khoa Đại Phước"

    # Detect Type (Imaging vs Lab)
    if any(k in text.upper() for k in ["CT SCAN", "MSCT", "CỘNG HƯỞNG TỪ", "MRI", "SIÊU ÂM"]):
        draft["type"] = "imaging"
        draft["title"] = "Kết Quả Chẩn Đoán Hình Ảnh"
    else:
        draft["type"] = "lab"
        draft["title"] = "Xét Nghiệm Máu & Sinh Hóa"

    # Extract Common Lab Values by parsing lines and skipping reference ranges in parentheses
    lines = text.split("\n")
    patterns = {
        "WBC": r"WBC",
        "NEU_pct": r"(?:%\s*Neu|NEU%)",
        "LYM_pct": r"(?:%\s*Lym|LYM%)",
        "RBC": r"RBC",
        "Hb": r"(?:Hb|HGB)",
        "Hct": r"(?:Hct|HCT)",
        "PLT": r"PLT",
        "MCV": r"MCV",
        "MCH": r"MCH\b",
        "MCHC": r"MCHC",
        "RDW": r"RDW",
        "Creatinine": r"Creatinin",
        "eGFR": r"eGFR",
        "Urea": r"Ure[a]?",
        "SGOT_AST": r"(?:SGOT|AST)",
        "SGPT_ALT": r"(?:SGPT|ALT)",
        "GGT": r"GGT",
        "Total_Protein": r"(?:Protid|Protein)[,\s]+Total",
        "Albumin": r"Albumin",
        "CEA": r"CEA",
        "NSE": r"NSE",
        "Cyfra_21_1": r"Cyfra",
        "D_Dimer": r"D-Dimer"
    }

    for key, pat in patterns.items():
        for line in lines:
            if re.search(pat, line, re.IGNORECASE):
                # If line has closing paren (reference range), result is after the closing paren
                m = re.search(r'\)\s*(?:10\^[\d]+/L|[a-zA-Z/%²³\^]+)?\s*([\d\.]+)', line)
                if not m:
                    # Fallback for lines without parens
                    m = re.search(rf'{pat}[^\d]*([\d\.]+)', line, re.IGNORECASE)
                if m:
                    try:
                        val = float(m.group(1))
                        draft["lab_results"][key] = {"val": val}
                        if key in ["Creatinine", "eGFR", "Hb", "PLT", "CEA", "D_Dimer"]:
                            draft["metrics"][key.lower()] = val
                        break
                    except:
                        pass

    # Extract Conclusion
    kl_match = re.search(r"Kết luận\s*:\s*([^.\n]+(?:\n[^\n]+){1,6})", text, re.IGNORECASE)
    if kl_match:
        draft["conclusion"] = kl_match.group(1).strip()
        draft["summary"] = draft["conclusion"][:150]
    elif draft["lab_results"]:
        summary_items = []
        if "Hb" in draft["lab_results"]: summary_items.append(f"Hb {draft['lab_results']['Hb']['val']} g/dL")
        if "eGFR" in draft["lab_results"]: summary_items.append(f"eGFR {draft['lab_results']['eGFR']['val']} mL/min")
        if "WBC" in draft["lab_results"]: summary_items.append(f"WBC {draft['lab_results']['WBC']['val']}")
        draft["summary"] = "Xét nghiệm: " + ", ".join(summary_items)

    return draft

ALLOWED_EXTENSIONS = {
    'pdf', 'jpg', 'jpeg', 'png', 'heic', 'webp', 
    'mp3', 'm4a', 'wav', 
    'mp4', 'mov', 'webm', 
    'dcm', 'zip'
}

@app.route("/api/media/<path:filename>")
def get_media(filename):
    import urllib.parse
    filename = urllib.parse.unquote(filename)
    parent_path = os.path.join(PARENT_DIR, filename)
    if os.path.exists(parent_path):
        return send_file(parent_path)
    static_pdf = os.path.join(STATIC_FOLDER, "pdfs", filename)
    if os.path.exists(static_pdf):
        return send_file(static_pdf)
    upload_path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(upload_path):
        return send_file(upload_path)
    return jsonify({"error": "Media not found"}), 404

@app.route("/api/avatar", methods=["GET", "POST"])
def handle_avatar():
    avatar_file = os.path.join(UPLOAD_FOLDER, "patient_avatar.png")
    if request.method == "POST":
        if "avatar" not in request.files:
            return jsonify({"error": "No avatar file"}), 400
        f = request.files["avatar"]
        f.save(avatar_file)
        return jsonify({"success": True, "url": "/api/avatar?" + str(int(datetime.now().timestamp()))})
    else:
        if os.path.exists(avatar_file):
            return send_file(avatar_file)
        return jsonify({"error": "No avatar set"}), 404

@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400
    
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Định dạng .{ext} không được hỗ trợ. Vui lòng tải PDF, Ảnh, Audio, Video hoặc file CT Scan"}), 400

    filename = file.filename
    dest_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(dest_path)

    full_text = ""
    page_count = 1
    media_type = "document"

    if ext == 'pdf':
        try:
            doc = fitz.open(dest_path)
            page_count = len(doc)
            for page in doc:
                full_text += page.get_text() + "\n"
        except Exception as e:
            print(f"Error reading PDF {filename}: {e}")

        if len(full_text.strip()) < 50:
            try:
                with pdfplumber.open(dest_path) as pdf:
                    for page in pdf.pages:
                        t = page.extract_text()
                        if t: full_text += t + "\n"
            except Exception as e:
                print(f"pdfplumber error: {e}")
        draft = parse_extracted_text(full_text, filename)
    elif ext in ['jpg', 'jpeg', 'png', 'heic', 'webp']:
        media_type = "image"
        draft = {
            "id": f"rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "facility": "Bệnh viện FV / Cơ sở Y tế",
            "doctor": "",
            "type": "imaging",
            "title": "Ảnh Chụp Phiếu Khám / Chẩn Đoán",
            "summary": f"Hình ảnh tài liệu y tế ({filename}) tải lên từ thiết bị di động hoặc máy quét.",
            "details": {"Tập tin hình ảnh": filename},
            "lab_results": {},
            "metrics": {},
            "pdf_file": filename,
            "media_url": f"/api/media/{filename}",
            "media_type": "image"
        }
        # Check filename for date YYYYMMDD
        fn_match = re.search(r"(202\d)(\d{2})(\d{2})", filename)
        if fn_match:
            draft["date"] = f"{fn_match.group(1)}-{fn_match.group(2)}-{fn_match.group(3)}"
    elif ext in ['mp3', 'm4a', 'wav']:
        media_type = "audio"
        draft = {
            "id": f"rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "facility": "Bệnh viện FV / Phòng khám",
            "doctor": "",
            "type": "comprehensive",
            "title": "Ghi Âm Lời Dặn & Tư Vấn Bác Sĩ",
            "summary": f"Bản ghi âm lời dặn, giải thích kết quả xét nghiệm hoặc hội chẩn của bác sĩ ({filename}).",
            "details": {"Tập tin âm thanh": filename},
            "lab_results": {},
            "metrics": {},
            "pdf_file": filename,
            "media_url": f"/api/media/{filename}",
            "media_type": "audio"
        }
    elif ext in ['mp4', 'mov', 'webm']:
        media_type = "video"
        draft = {
            "id": f"rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "facility": "Bệnh viện FV",
            "doctor": "",
            "type": "imaging",
            "title": "Video Siêu Âm / Nội Soi Y Khoa",
            "summary": f"Video chẩn đoán hình ảnh lâm sàng ({filename}) - Siêu âm tim Doppler / Nội soi phế quản.",
            "details": {"Tập tin video": filename},
            "lab_results": {},
            "metrics": {},
            "pdf_file": filename,
            "media_url": f"/api/media/{filename}",
            "media_type": "video"
        }
    elif ext in ['dcm', 'zip']:
        media_type = "ct_scan"
        draft = {
            "id": f"rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "facility": "Khoa Chẩn Đoán Hình Ảnh",
            "doctor": "",
            "type": "imaging",
            "title": "Dữ Liệu CT Scan / Cắt Lớp DICOM",
            "summary": f"Tập tin dữ liệu ảnh cắt lớp vi tính CT Scan / MRI định dạng DICOM ({filename}).",
            "details": {"Dữ liệu lát cắt": filename},
            "lab_results": {},
            "metrics": {},
            "pdf_file": filename,
            "media_url": f"/api/media/{filename}",
            "media_type": "ct_scan"
        }

    is_scanned = len(full_text.strip()) < 30 and ext == 'pdf'
    draft["is_scanned"] = is_scanned
    draft["page_count"] = page_count

    return jsonify({
        "success": True,
        "filename": filename,
        "is_scanned": is_scanned,
        "page_count": page_count,
        "media_type": media_type,
        "draft": draft
    })

if __name__ == "__main__":
    print(f"Starting Medical Dashboard Server on http://localhost:5050 ...")
    app.run(host="0.0.0.0", port=5050, debug=False)
