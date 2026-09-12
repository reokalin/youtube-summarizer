import os
import re
import json
import streamlit as st
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from google import genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

st.set_page_config(page_title="YouTube Summarizer", page_icon="🎬", layout="centered")

st.title("🎬 YouTube AI Summarizer")
st.caption("URLを入力すると、Gemini 3.6が要約してGoogleドライブへ直接保存します。")

# Secrets情報の読み込み
try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    GOOGLE_DRIVE_FOLDER_ID = st.secrets["GOOGLE_DRIVE_FOLDER_ID"]
    CLIENT_ID = st.secrets["GOOGLE_CLIENT_ID"]
    CLIENT_SECRET = st.secrets["GOOGLE_CLIENT_SECRET"]
    REFRESH_TOKEN = st.secrets["GOOGLE_REFRESH_TOKEN"]
except Exception as e:
    st.error("設定情報（Secrets）が見つかりません。Streamlitの設定を行ってください。")
    st.stop()

def get_drive_service():
    creds = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    return build('drive', 'v3', credentials=creds)

def extract_video_id(url):
    match = re.search(r'(?:v=|\/|shorts\/)([0-9A-Za-z_-]{11})', url)
    return match.group(1) if match else None

video_url_input = st.text_input("YouTube動画URLを入力", placeholder="https://www.youtube.com/watch?v=...")

if st.button("要約してGoogleドライブへ保存", type="primary", use_container_width=True):
    if not video_url_input:
        st.warning("URLを入力してください。")
    else:
        video_id = extract_video_id(video_url_input)
        if not video_id:
            st.error("有効なYouTube URLではありません。")
        else:
            # 1. 字幕取得（正しい API 呼出構文）
            with st.spinner("1. 動画の字幕・文字起こしデータを取得中..."):
                transcript_text = ""
                try:
                    yt_api = YouTubeTranscriptApi()
                    fetched = yt_api.fetch(video_id, languages=['ja', 'en'])
                    formatter = TextFormatter()
                    transcript_text = formatter.format_transcript(fetched)
                except Exception as e:
                    st.error(f"字幕データの取得に失敗しました。字幕が無効な動画の可能性があります: {e}")
                    st.stop()

            # 2. Gemini要約
            with st.spinner("2. Gemini 3.6 が文字起こしテキストを解析中..."):
                try:
                    client = genai.Client(api_key=GEMINI_API_KEY)
                    
                    prompt = f"""
以下のYouTube動画の文字起こしテキストを読み、わかりやすく要約してください。

【対象動画URL】
https://www.youtube.com/watch?v={video_id}

【文字起こしテキスト】
{transcript_text[:30000]}

【出力フォーマット】
■ 動画概要（3行で要約）
・
・
・

■ 主なキーポイント・要点
・
・

■ 詳細まとめ・結論
"""
                    response = client.models.generate_content(
                        model='gemini-3.6-flash',
                        contents=prompt
                    )
                    summary_result = response.text
                except Exception as e:
                    st.error(f"Gemini API 解析エラー: {e}")
                    st.stop()

            # 3. Googleドライブ保存
            with st.spinner("3. Googleドライブへ保存中..."):
                try:
                    service = get_drive_service()
                    file_name = f"summary_{video_id}.txt"
                    file_content = f"URL: https://www.youtube.com/watch?v={video_id}\n\n====================\n【AI要約結果】\n====================\n\n{summary_result}"

                    file_metadata = {
                        'name': file_name,
                        'parents': [GOOGLE_DRIVE_FOLDER_ID]
                    }
                    media = MediaInMemoryUpload(file_content.encode('utf-8'), mimetype='text/plain')
                    
                    uploaded_file = service.files().create(
                        body=file_metadata,
                        media_body=media,
                        fields='id'
                    ).execute()
                    
                    st.success("🎉 要約が完了し、Googleドライブへの保存が成功しました！")
                    st.markdown(summary_result)
                except Exception as e:
                    st.error(f"Googleドライブ保存エラー: {e}")
