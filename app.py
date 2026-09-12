import os
import re
import json
import tempfile
import streamlit as st
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from google import genai
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(page_title="YouTube Summarizer", page_icon="🎬", layout="centered")

st.title("🎬 YouTube AI Summarizer")
st.caption("URLを入力すると、Gemini 3.6が要約してGoogleドライブへ直接保存します。")

# Secrets情報の読み込み
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    GOOGLE_DRIVE_FOLDER_ID = st.secrets["GOOGLE_DRIVE_FOLDER_ID"]
    SA_JSON_STR = st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]
except Exception as e:
    st.error("設定情報（Secrets）が見つかりません。設定を行ってください。")
    st.stop()

def get_drive_instance():
    gauth = GoogleAuth()
    scope = ["https://www.googleapis.com/auth/drive"]
    
    sa_info = json.loads(SA_JSON_STR)
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        json.dump(sa_info, f)
        temp_json_path = f.name

    gauth.service_account_file = temp_json_path
    gauth.credentials = ServiceAccountCredentials.from_json_keyfile_name(temp_json_path, scope)
    return GoogleDrive(gauth)

def extract_video_id(url):
    match = re.search(r'(?:v=|\/|shorts\/)([0-9A-Za-z_-]{11})', url)
    return match.group(1) if match else None

video_url = st.text_input("YouTube動画URLを入力", placeholder="https://www.youtube.com/watch?v=...")

if st.button("要約してGoogleドライブへ保存", type="primary", use_container_width=True):
    if not video_url:
        st.warning("URLを入力してください。")
    else:
        video_id = extract_video_id(video_url)
        if not video_id:
            st.error("有効なYouTube URLではありません。")
        else:
            with st.spinner("1. 字幕データを取得中..."):
                try:
                    api = YouTubeTranscriptApi()
                    fetched = api.fetch(video_id, languages=['ja', 'en'])
                    formatter = TextFormatter()
                    transcript_text = formatter.format_transcript(fetched)
                except Exception as e:
                    st.error(f"字幕の取得に失敗しました: {e}")
                    st.stop()

            with st.spinner("2. Gemini 3.6 で要約を作成中..."):
                try:
                    client = genai.Client(api_key=GEMINI_API_KEY)
                    prompt = f"以下のYouTube動画の文字起こしテキストを読み、構造化してわかりやすく要約してください。\n\n【文字起こし】\n{transcript_text[:30000]}"
                    response = client.models.generate_content(
                        model='gemini-3.6-flash',
                        contents=prompt
                    )
                    summary_result = response.text
                except Exception as e:
                    st.error(f"Gemini API エラー: {e}")
                    st.stop()

            with st.spinner("3. Googleドライブへ保存中..."):
                try:
                    drive = get_drive_instance()
                    file_name = f"summary_{video_id}.txt"
                    file_content = f"URL: {video_url}\n\n====================\n【AI要約結果】\n====================\n\n{summary_result}"

                    file_metadata = {
                        'title': file_name,
                        'mimeType': 'text/plain',
                        'parents': [{'id': GOOGLE_DRIVE_FOLDER_ID}]
                    }
                    drive_file = drive.CreateFile(file_metadata)
                    drive_file.SetContentString(file_content)
                    drive_file.Upload()
                    
                    st.success("🎉 要約が完了し、Googleドライブへの保存が成功しました！")
                    st.markdown(summary_result)
                except Exception as e:
                    st.error(f"Googleドライブ保存エラー: {e}")
