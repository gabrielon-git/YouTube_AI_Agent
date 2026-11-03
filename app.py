from flask import Flask, request, render_template_string
import os
import time
from google import genai

app = Flask(__name__)

def generate_script_with_retry(client, prompt, max_retries=3):
    """Tries to generate a script, retrying on 503 errors."""
    models_to_try = ['gemini-3.8-flash']
    
    for model_name in models_to_try:
        for attempt in range(max_retries):
            try:
                print(f"Attempting with {model_name} (try {attempt + 1})...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                return response.text
            except Exception as e:
                error_str = str(e)
                if "503" in error_str or "UNAVAILABLE" in error_str:
                    wait_time = 2 ** attempt
                    print(f"Got 503. Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    raise e
    raise Exception("All models failed after multiple retries. Please try again later.")

@app.route('/', methods=['GET', 'POST'])
def home():
    script = None
    if request.method == 'POST':
        topic = request.form['topic']
        video_format = request.form.get('video_format', 'short')
        api_key = os.environ.get("GEMINI_API_KEY")
        
        if not api_key:
            script = "Error: API Key is missing."
        else:
            try:
                client = genai.Client(api_key=api_key)
                
                # Change the prompt based on the selected format
                if video_format == 'short':
                    prompt = f"Write a highly engaging, fast-paced 60-second YouTube Short script about: {topic}. Include visual cues in brackets."
                else:
                    prompt = f"Write a detailed, structured, long-form YouTube video script (about 10 minutes long) about: {topic}. Include an intro, 3 main chapters, an outro, and visual cues in brackets."
                
                script = generate_script_with_retry(client, prompt)
            except Exception as e:
                script = f"Error: {str(e)}"
            
    # We added the dropdown menu to the HTML below
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>AI Studio</title><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="font-family: Arial; text-align: center; margin-top: 30px; background-color: #f0f2f5;">
        <h1 style="color: #333;">🚀 YouTube AI Studio</h1>
        
        <div style="padding: 20px; background: white; border-radius: 10px; display: inline-block; width: 90%; max-width: 500px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            <form method="POST">
                <!-- Topic Input -->
                <input type="text" name="topic" placeholder="Enter video topic..." style="padding: 10px; width: 80%; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 5px;">
                
                <!-- Video Format Dropdown -->
                <select name="video_format" style="padding: 10px; width: 85%; margin-bottom: 15px; border: 1px solid #ccc; border-radius: 5px; background-color: white; font-size: 16px;">
                    <option value="short">Short-form (Under 60 seconds)</option>
                    <option value="long">Long-form (10+ minutes)</option>
                </select>
                <br>
                
                <button type="submit" style="padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; width: 85%;">Generate Script</button>
            </form>
        </div>
        
        {% if script %}
        <div style="margin-top: 20px; padding: 20px; background: white; border-radius: 10px; text-align: left; width: 90%; max-width: 500px; margin-left: auto; margin-right: auto; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            <h3>Your Script:</h3>
            <p style="white-space: pre-wrap;">{{ script }}</p>
        </div>
        {% endif %}
    </body>
    </html>
    """
    return render_template_string(html, script=script)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7070)
