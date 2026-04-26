from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from sqlalchemy import func
import requests
import json
import uuid
import os
import time
import base64
import re
from werkzeug.utils import secure_filename
import PyPDF2
import csv
import io

app = Flask(__name__)
app.secret_key = "super_secret_edubot_key"

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- DATABASE CONFIGURATION ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///edubot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- LOGIN MANAGER CONFIGURATION ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'

# ===================== CONFIGURATION FOR GEMINI MODELS =====================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", '''Enter API KEY''')
GEMINI_MODEL_GENERATOR = "gemini-2.5-flash"
GEMINI_MODEL_EVALUATOR = "gemini-2.5-flash"
GEMINI_OCR_URL = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"


# ===================== DATABASE MODELS =====================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(20), nullable=False)

    authored_tests = db.relationship('TestSession', backref='author', lazy=True, cascade="all, delete-orphan")
    submissions = db.relationship('TestSubmission', foreign_keys='TestSubmission.student_id', backref='student_ref',
                                  lazy=True, cascade="all, delete-orphan")
    flashcard_sets = db.relationship('FlashcardSet', backref='student_ref', lazy=True, cascade="all, delete-orphan")


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class TestSession(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_practice = db.Column(db.Boolean, default=False)
    generation_method = db.Column(db.String(50))
    evaluation_pattern = db.Column(db.String(50))
    test_params = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    questions = db.relationship('Question', backref='test', lazy=True, cascade="all, delete-orphan")
    submissions = db.relationship('TestSubmission', backref='test', lazy=True, cascade="all, delete-orphan")


class Question(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    test_id = db.Column(db.String(36), db.ForeignKey('test_session.id'), nullable=False)
    order_num = db.Column(db.Integer)
    question_text = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(50))
    options = db.Column(db.Text)
    correct_answer = db.Column(db.Text)
    grading_rubric = db.Column(db.Text)
    marks = db.Column(db.Float)


class TestSubmission(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    test_id = db.Column(db.String(36), db.ForeignKey('test_session.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    overall_score = db.Column(db.Float, default=0.0)
    total_possible_score = db.Column(db.Float, default=0.0)
    is_evaluated = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    answers = db.relationship('StudentAnswer', backref='submission', lazy=True, cascade="all, delete-orphan")


class StudentAnswer(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    submission_id = db.Column(db.String(36), db.ForeignKey('test_submission.id'), nullable=False)
    question_id = db.Column(db.String(36), db.ForeignKey('question.id'), nullable=False)
    student_answer_text = db.Column(db.Text)
    processed_answer = db.Column(db.Text)
    score_awarded = db.Column(db.Float, default=0.0)
    reasoning_and_analysis = db.Column(db.Text)
    feedback_html = db.Column(db.Text)
    deductions = db.Column(db.Text)


class FlashcardSet(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    cards = db.relationship('Flashcard', backref='flashcard_set', lazy=True, cascade="all, delete-orphan")


class Flashcard(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    set_id = db.Column(db.String(36), db.ForeignKey('flashcard_set.id'), nullable=False)
    front_concept = db.Column(db.Text, nullable=False)
    back_definition = db.Column(db.Text, nullable=False)


# ===================== HELPER FUNCTIONS & LANG STRINGS =====================
LANG_STRINGS = {
    'en': {
        'landing_title': "Welcome to Gemini EduBot", 'select_role': "Please select your role:",
        'role_teacher': "Teacher", 'role_student': "Student",
        'welcome_message': "Teacher Dashboard: Generate Test", 'choose_lang': "Choose Language:",
        'subject': "Subject: ", 'topic': "Topic: ", 'board': "Board / Exam Type: ", 'age': "Age: ",
        'student_class': "Class: ",
        'gender': "Gender: ", 'avg_marks': "Average Marks (%): ", 'prev_tests': "Previous 5 Tests: ",
        'num_questions': "Number of Questions: ", 'question_type': "Type of Questions: ", 'objective_type': "Objective",
        'subjective_type': "Subjective", 'mixed_type': "Mixed", 'multiple_options_type': "Multiple Options",
        'section_wise_marking_type': "Section-wise Marking", 'question_hardness': "Question Hardness: ",
        'basic_hardness': "Basic", 'medium_hardness': "Medium", 'hard_hardness': "Hard", 'scholar_hardness': "Scholar",
        'master_hardness': "Master", 'motive': "Motive of the test: ", 'ask_repetitive': "Ask Repetitive Questions?",
        'yes': "Yes", 'no': "No", 'generate_questions': "Generate Questions",
        'failed_to_generate_test': "Failed to generate test. Please try again.",
        'enter_custom_prompt': "Enter your custom prompt for question generation: ",
        'eval_pattern': "Evaluation Pattern: ",
        'positive_eval_option': "Positive", 'negative_eval_option': "Negative", 'basic_eval_option': "Basic",
        'medium_eval_option': "Medium", 'hard_eval_option': "Hard", 'scholar_eval_option': "Scholar",
        'master_eval_option': "Master",
        'take_test': "Take the Test", 'question_num': "Question {i} ({marks} marks): {text}",
        'your_answer_obj': "Your answer: ",
        'your_answer_subj': "Your answer: ", 'submit_answer': "Submit Answer", 'evaluate_test': "Evaluate Test",
        'test_results': "Test Results", 'overall_score': "Overall Score: {score} / {possible}",
        'question_section_header': "Question {i}", 'question_text_label': "Question: ",
        'your_answer_label': "Your Answer: ",
        'ideal_answer_label': "Ideal Answer: ", 'score_label': "Score: {score} / {possible}",
        'feedback_label': "Feedback: ",
        'deductions_label': "Deductions:", 'deduction_item': "  - {description} (-{marks_deducted} marks)",
        'api_key_not_set': "Gemini API Key is not set.",
        'unexpected_api_error': "An unexpected error occurred during API call: {error}",
        'api_instruction_lang': "Generate all content in {lang_name}. ",
        'upload_test_paper_field': "Upload a Test Paper (PDF): ",
        'pdf_extraction_error': "Error extracting text from PDF.",
        'pdf_options_title': "PDF Upload Options",
        'pdf_options_instruction': "Provide additional parameters for generating questions from PDF.",
        'num_questions_for_pdf': "Number of Questions (1-40) for PDF:",
        'generate_questions_only': "Generate Questions Only (for review/printing)",
        'generate_and_take_test': "Generate Questions and Take Test", 'proceed': "Proceed",
        'teacher_analytics': "Teacher Analytics Dashboard", 'past_tests': "Your Past Tests",
        'test_date': "Date Created",
        'generation_type': "Type", 'view_analytics': "View Analytics",
        'no_tests_yet': "You haven't created any tests yet.",
        'test_overview': "Test Overview", 'total_submissions': "Total Submissions",
        'average_score': "Average Class Score",
        'student_results': "Student Results", 'submission_id': "Student ID (Anon)", 'score': "Score",
        'status': "Status",
        'evaluated': "Evaluated", 'pending': "Pending", 'back_to_dashboard': "← Back to Dashboard",
        'view_student_test': "View Full Test",
        'question_bank': "Question Bank", 'search_questions': "Search questions...",
        'create_test_from_selected': "Create Test with Selected",
        'admin_dashboard': "Admin Dashboard", 'system_overview': "System Overview", 'total_teachers': "Total Teachers",
        'total_students': "Total Students", 'total_tests': "Total Tests Created", 'user_management': "User Management",
        'test_monitoring': "Test Monitoring", 'smart_flashcards': "Smart Flashcards"
    },
    'hi': {
        'landing_title': "Gemini EduBot में आपका स्वागत है", 'select_role': "कृपया अपनी भूमिका चुनें:",
        'role_teacher': "शिक्षक", 'role_student': "छात्र",
        'welcome_message': "शिक्षक डैशबोर्ड: टेस्ट बनाएं", 'choose_lang': "भाषा चुनें:",
        'subject': "विषय: ", 'topic': "टॉपिक: ", 'board': "बोर्ड: ", 'age': "आयु: ", 'student_class': "कक्षा: ",
        'gender': "लिंग: ", 'avg_marks': "औसत अंक (%): ", 'prev_tests': "पिछले 5 टेस्ट: ",
        'num_questions': "प्रश्नों की संख्या: ", 'question_type': "प्रश्नों के प्रकार: ",
        'objective_type': "वस्तुनिष्ठ",
        'subjective_type': "विषयपरक", 'mixed_type': "मिश्रित", 'multiple_options_type': "बहुविकल्पीय",
        'section_wise_marking_type': "खंड-वार अंकन", 'question_hardness': "प्रश्नों की कठिनाई: ",
        'basic_hardness': "बेसिक", 'medium_hardness': "मध्यम", 'hard_hardness': "कठिन", 'scholar_hardness': "विद्वान",
        'master_hardness': "मास्टर", 'motive': "टेस्ट का उद्देश्य: ",
        'ask_repetitive': "क्या दोहराए जाने वाले प्रश्न पूछें?",
        'yes': "हाँ", 'no': "नहीं", 'generate_questions': "प्रश्न जेनरेट करें",
        'failed_to_generate_test': "टेस्ट जेनरेट करने में विफल।",
        'enter_custom_prompt': "कस्टम प्रॉम्प्ट दर्ज करें: ", 'eval_pattern': "मूल्यांकन पैटर्न: ",
        'positive_eval_option': "सकारात्मक", 'negative_eval_option': "नकारात्मक", 'basic_eval_option': "बेसिक",
        'medium_eval_option': "मध्यम", 'hard_eval_option': "कठिन", 'scholar_eval_option': "विद्वान",
        'master_eval_option': "मास्टर",
        'take_test': "टेस्ट दें", 'question_num': "प्रश्न {i} ({marks} अंक): {text}", 'your_answer_obj': "आपका उत्तर: ",
        'your_answer_subj': "आपका उत्तर: ", 'submit_answer': "उत्तर सबमिट करें", 'evaluate_test': "मूल्यांकन करें",
        'test_results': "टेस्ट परिणाम", 'overall_score': "कुल स्कोर: {score} / {possible}",
        'question_section_header': "प्रश्न {i}", 'question_text_label': "प्रश्न: ", 'your_answer_label': "आपका उत्तर: ",
        'ideal_answer_label': "आदर्श उत्तर: ", 'score_label': "स्कोर: {score} / {possible}",
        'feedback_label': "फीडबैक: ",
        'deductions_label': "कटौती:", 'deduction_item': "  - {description} (-{marks_deducted} अंक)",
        'api_key_not_set': "API कुंजी सेट नहीं है।", 'unexpected_api_error': "त्रुटि: {error}",
        'api_instruction_lang': "सामग्री {lang_name} में जेनरेट करें। ",
        'upload_test_paper_field': "PDF अपलोड करें: ", 'pdf_extraction_error': "PDF से टेक्स्ट निकालने में त्रुटि।",
        'pdf_options_title': "PDF विकल्प", 'pdf_options_instruction': "पैरामीटर प्रदान करें।",
        'num_questions_for_pdf': "प्रश्नों की संख्या (1-40):", 'generate_questions_only': "केवल प्रश्न जेनरेट करें",
        'generate_and_take_test': "प्रश्न जेनरेट करें और टेस्ट दें", 'proceed': "आगे बढ़ें",
        'teacher_analytics': "शिक्षक एनालिटिक्स", 'past_tests': "पिछले टेस्ट", 'test_date': "तिथि",
        'generation_type': "प्रकार", 'view_analytics': "एनालिटिक्स देखें",
        'no_tests_yet': "कोई टेस्ट नहीं बनाया गया है।",
        'test_overview': "टेस्ट अवलोकन", 'total_submissions': "कुल सबमिशन", 'average_score': "औसत स्कोर",
        'student_results': "छात्र परिणाम", 'submission_id': "छात्र आईडी", 'score': "स्कोर", 'status': "स्थिति",
        'evaluated': "मूल्यांकित", 'pending': "लंबित", 'back_to_dashboard': "← डैशबोर्ड पर वापस जाएं",
        'view_student_test': "पूरा टेस्ट देखें",
        'question_bank': "प्रश्न बैंक", 'search_questions': "खोजें...", 'create_test_from_selected': "टेस्ट बनाएं",
        'admin_dashboard': "व्यवस्थापक डैशबोर्ड", 'system_overview': "सिस्टम अवलोकन", 'total_teachers': "कुल शिक्षक",
        'total_students': "कुल छात्र", 'total_tests': "कुल टेस्ट", 'user_management': "उपयोगकर्ता प्रबंधन",
        'test_monitoring': "टेस्ट निगरानी", 'smart_flashcards': "स्मार्ट फ्लैशकार्ड"
    },
    'mr': {
        'landing_title': "Gemini EduBot मध्ये आपले स्वागत आहे", 'select_role': "भूमिका निवडा:",
        'role_teacher': "शिक्षक", 'role_student': "विद्यार्थी",
        'welcome_message': "शिक्षक डॅशबोर्ड: टेस्ट तयार करा", 'choose_lang': "भाषा निवडा:",
        'subject': "विषय: ", 'topic': "टॉपिक: ", 'board': "बोर्ड: ", 'age': "वय: ", 'student_class': "इयत्ता: ",
        'gender': "लिंग: ", 'avg_marks': "सरासरी गुण (%): ", 'prev_tests': "मागील 5 टेस्ट: ",
        'num_questions': "प्रश्नांची संख्या: ", 'question_type': "प्रश्नांचा प्रकार: ", 'objective_type': "वस्तुनिष्ठ",
        'subjective_type': "व्यक्तिनिष्ठ", 'mixed_type': "मिश्रित", 'multiple_options_type': "बहुपर्यायी",
        'section_wise_marking_type': "विभागानुसार गुण", 'question_hardness': "काठिण्य पातळी: ",
        'basic_hardness': "बेसिक", 'medium_hardness': "मध्यम", 'hard_hardness': "कठीण", 'scholar_hardness': "विद्वान",
        'master_hardness': "मास्टर", 'motive': "टेस्टचा उद्देश: ", 'ask_repetitive': "पुन्हा विचारू का?",
        'yes': "होय", 'no': "नाही", 'generate_questions': "प्रश्न जनरेट करा",
        'failed_to_generate_test': "अयशस्वी. पुन्हा प्रयत्न करा.",
        'enter_custom_prompt': "कस्टम प्रॉम्प्ट: ", 'eval_pattern': "मूल्यांकन पॅटर्न: ",
        'positive_eval_option': "सकारात्मक", 'negative_eval_option': "नकारात्मक", 'basic_eval_option': "बेसिक",
        'medium_eval_option': "मध्यम", 'hard_eval_option': "कठीण", 'scholar_eval_option': "विद्वान",
        'master_eval_option': "मास्टर",
        'take_test': "टेस्ट द्या", 'question_num': "प्रश्न {i} ({marks} गुण): {text}",
        'your_answer_obj': "तुमचे उत्तर: ",
        'your_answer_subj': "तुमचे उत्तर: ", 'submit_answer': "उत्तर सबमिट करा", 'evaluate_test': "मूल्यांकन करा",
        'test_results': "निकाल", 'overall_score': "एकूण स्कोअर: {score} / {possible}",
        'question_section_header': "प्रश्न {i}", 'question_text_label': "प्रश्न: ",
        'your_answer_label': "तुमचे उत्तर: ",
        'ideal_answer_label': "आदर्श उत्तर: ", 'score_label': "स्कोअर: {score} / {possible}",
        'feedback_label': "फीडबॅक: ",
        'deductions_label': "कपात:", 'deduction_item': "  - {description} (-{marks_deducted} गुण)",
        'api_key_not_set': "API की नाही.", 'unexpected_api_error': "त्रुटी: {error}",
        'api_instruction_lang': "सामग्री {lang_name} मध्ये जनरेट करा. ",
        'upload_test_paper_field': "PDF अपलोड करा: ", 'pdf_extraction_error': "PDF मधून मजकूर काढताना त्रुटी.",
        'pdf_options_title': "PDF पर्याय", 'pdf_options_instruction': "पॅरामीटर्स प्रदान करा.",
        'num_questions_for_pdf': "प्रश्नांची संख्या (1-40):", 'generate_questions_only': "फक्त प्रश्न जनरेट करा",
        'generate_and_take_test': "प्रश्न आणि टेस्ट", 'proceed': "पुढे जा",
        'teacher_analytics': "शिक्षक ॲनालिटिक्स", 'past_tests': "मागील टेस्ट्स", 'test_date': "तारीख",
        'generation_type': "प्रकार", 'view_analytics': "ॲनालिटिक्स पहा", 'no_tests_yet': "कोणतीही टेस्ट नाही.",
        'test_overview': "टेस्टचा आढावा", 'total_submissions': "एकूण सबमिशन", 'average_score': "सरासरी स्कोअर",
        'student_results': "विद्यार्थ्यांचे निकाल", 'submission_id': "विद्यार्थी आयडी", 'score': "स्कोअर",
        'status': "स्थिती",
        'evaluated': "मूल्यांकन केले", 'pending': "प्रलंबित", 'back_to_dashboard': "← डॅशबोर्डवर परत",
        'view_student_test': "पूर्ण टेस्ट पहा",
        'question_bank': "प्रश्न बँक", 'search_questions': "शोधा...", 'create_test_from_selected': "टेस्ट तयार करा",
        'admin_dashboard': "अ‍ॅडमिन डॅशबोर्ड", 'system_overview': "सिस्टम विहंगावलोकन", 'total_teachers': "एकूण शिक्षक",
        'total_students': "एकूण विद्यार्थी", 'total_tests': "एकूण टेस्ट", 'user_management': "वापरकर्ता व्यवस्थापन",
        'test_monitoring': "टेस्ट मॉनिटरिंग", 'smart_flashcards': "स्मार्ट फ्लॅशकार्ड्स"
    }
}

current_language = 'en'
lang_names = {'en': 'English', 'hi': 'Hindi', 'mr': 'Marathi'}


def get_string(key, **kwargs):
    s = LANG_STRINGS.get(current_language, LANG_STRINGS['en']).get(key, f"MISSING_STRING_KEY: {key}")
    return s.format(**kwargs)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text_from_pdf(filepath):
    text = ""
    try:
        with open(filepath, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            for page_num in range(len(reader.pages)): text += reader.pages[page_num].extract_text()
        return text
    except:
        return ""


def encode_image_to_base64(image_path: str) -> str:
    with open(image_path, "rb") as img_file: return base64.b64encode(img_file.read()).decode("utf-8")


def extract_text_from_image(image_path: str, mime="image/jpeg") -> str:
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_API_KEY_HERE":
        return "ERROR: Valid API Key missing."
    try:
        payload = {"contents": [{"parts": [{"text": "Extract all readable text from this image."}, {
            "inline_data": {"mime_type": mime, "data": encode_image_to_base64(image_path)}}]}]}
        resp = requests.post(GEMINI_OCR_URL, json=payload)
        resp.raise_for_status()
        extracted_text = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return clean_gemini_json_output(extracted_text) if extracted_text else "No text found."
    except Exception as e:
        return f"[ERROR] OCR failed: {e}"


def generate_unique_id():
    return str(uuid.uuid4())


def save_test_to_db(test_id, author_id, test_params, questions_list, evaluation_pattern, is_practice=False):
    new_test = TestSession(
        id=test_id, author_id=author_id, is_practice=is_practice,
        generation_method=test_params.get('generation_method', 'unknown'),
        evaluation_pattern=evaluation_pattern, test_params=json.dumps(test_params)
    )
    db.session.add(new_test)
    for idx, q in enumerate(questions_list):
        new_q = Question(
            id=q['question_id'], test_id=test_id, order_num=idx, question_text=q['question_text'],
            question_type=q['question_type'], options=json.dumps(q.get('options', [])),
            correct_answer=json.dumps(q.get('correct_answer', '')) if isinstance(q.get('correct_answer'),
                                                                                 list) else str(
                q.get('correct_answer', '')),
            grading_rubric=json.dumps(q.get('grading_rubric', [])), marks=float(q.get('marks', 0))
        )
        db.session.add(new_q)
    db.session.commit()


# ===================== AI INTEGRATION =====================
def call_gemini_api(content_parts, model_name, response_schema=None, max_retries=3, initial_delay=1):
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_API_KEY_HERE":
        raise ValueError("Invalid or Missing Gemini API Key")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    payload = {"contents": [{"role": "user", "parts": content_parts}]}
    if response_schema: payload["generationConfig"] = {"responseMimeType": "application/json",
                                                       "responseSchema": response_schema}
    payload["safetySettings"] = [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}]

    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers={'Content-Type': 'application/json'}, params={'key': GEMINI_API_KEY},
                                     json=payload, timeout=90)
            response.raise_for_status()
            return response.json()["candidates"][0]["content"]["parts"][0]["text"]
        except requests.exceptions.RequestException as e:
            wait_time = initial_delay * (2 ** attempt)
            if e.response is not None and e.response.status_code == 429: wait_time = 30.0
            if attempt < max_retries - 1:
                time.sleep(wait_time)
            else:
                raise
        except Exception as e:
            raise RuntimeError(str(e))


QUESTION_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"question_id": {"type": "STRING"},
                                                                               "question_text": {"type": "STRING"},
                                                                               "question_type": {"type": "STRING"},
                                                                               "options": {"type": "ARRAY",
                                                                                           "items": {"type": "STRING"}},
                                                                               "correct_answer": {"type": "STRING"},
                                                                               "grading_rubric": {"type": "ARRAY",
                                                                                                  "items": {
                                                                                                      "type": "OBJECT",
                                                                                                      "properties": {
                                                                                                          "criteria": {
                                                                                                              "type": "STRING"},
                                                                                                          "marks": {
                                                                                                              "type": "NUMBER"}},
                                                                                                      "required": [
                                                                                                          "criteria",
                                                                                                          "marks"]}},
                                                                               "marks": {"type": "NUMBER"}},
                                              "required": ["question_id", "question_text", "question_type",
                                                           "correct_answer", "marks"]}}

BATCH_EVALUATION_SCHEMA = {"type": "ARRAY",
                           "items": {"type": "OBJECT", "properties": {"question_id": {"type": "STRING"},
                                                                      "reasoning_and_analysis": {"type": "STRING"},
                                                                      "score_awarded": {"type": "NUMBER"},
                                                                      "total_marks_possible": {"type": "NUMBER"},
                                                                      "feedback_text": {"type": "STRING"},
                                                                      "feedback_html": {"type": "STRING"},
                                                                      "deductions": {"type": "ARRAY",
                                                                                     "items": {"type": "OBJECT",
                                                                                               "properties": {"type": {
                                                                                                   "type": "STRING"},
                                                                                                              "description": {
                                                                                                                  "type": "STRING"},
                                                                                                              "marks_deducted": {
                                                                                                                  "type": "NUMBER"}},
                                                                                               "required": ["type",
                                                                                                            "description",
                                                                                                            "marks_deducted"]}}},
                                     "required": ["question_id", "reasoning_and_analysis", "score_awarded",
                                                  "total_marks_possible", "feedback_text", "feedback_html",
                                                  "deductions"]}}

FLASHCARD_SCHEMA = {"type": "ARRAY",
                    "items": {"type": "OBJECT", "properties": {"front": {"type": "STRING"}, "back": {"type": "STRING"}},
                              "required": ["front", "back"]}}


def clean_gemini_json_output(raw_text):
    if raw_text.startswith("```"):
        first_nl = raw_text.find('\n')
        if first_nl != -1:
            start_idx = first_nl + 1 if raw_text[3:first_nl].strip().lower() in ['json', 'text'] else 3
            last_tick = raw_text.rfind('```')
            if last_tick != -1 and last_tick > start_idx: return raw_text[start_idx:last_tick].strip()
    return raw_text.strip()


def process_generated_questions(generated_questions_raw_text):
    cleaned_text = clean_gemini_json_output(generated_questions_raw_text)
    generated_questions = json.loads(cleaned_text)
    processed_questions = []
    for q in generated_questions:

        # ⚡️ FIX: ALWAYS generate a secure unique ID, ignore the AI's fake IDs
        q['question_id'] = generate_unique_id()

        if q.get('question_type') == 'multiple-options' and isinstance(q['correct_answer'], str):
            try:
                q['correct_answer'] = json.loads(q['correct_answer'])
            except:
                q['correct_answer'] = [q['correct_answer']]
        processed_questions.append(q)
    return processed_questions


def generate_test_questions(params, lang_code='en', document_text=None, num_questions_from_pdf=None):
    prompt_text = f"""You are an expert Test Paper Generator. Subject: {params.get('subject', 'General')} Number of Questions: {num_questions_from_pdf or params.get('num_questions', 5)} Type: {params.get('question_type', 'mixed')} Hardness: {params.get('question_hardness', 'medium')} {f"Context Text: {document_text}" if document_text else ""} Provide a JSON array matching the required schema. Ensure marks are numbers. IMPORTANT: Respond with ONLY the JSON array."""
    try:
        return process_generated_questions(
            call_gemini_api([{"text": prompt_text}], GEMINI_MODEL_GENERATOR, response_schema=QUESTION_SCHEMA))
    except Exception as e:
        print(f"Error Generating Questions: {e}")
        return None


def generate_single_adaptive_question(subject, topic, difficulty, previous_questions_text):
    prompt_text = f"""You are an expert Adaptive Test Generator. Subject: {subject}. Topic: {topic}. 
    Generate EXACTLY ONE question. Type: objective. Hardness: {difficulty}. 
    DO NOT REPEAT OR BE SIMILAR TO THESE PREVIOUS QUESTIONS: {previous_questions_text}
    Provide a JSON array matching the required schema. Ensure marks are numbers. IMPORTANT: Respond with ONLY the JSON array containing exactly 1 object."""
    try:
        return process_generated_questions(
            call_gemini_api([{"text": prompt_text}], GEMINI_MODEL_GENERATOR, response_schema=QUESTION_SCHEMA))
    except Exception as e:
        return None


def generate_questions_from_custom_prompt(custom_user_prompt, lang_code='en', document_text=None):
    prompt_text = f"You are an expert Test Paper Generator.\n{f'Context: {document_text}' if document_text else ''}\nUser Prompt: {custom_user_prompt}\nGenerate JSON array matching schema."
    try:
        return process_generated_questions(
            call_gemini_api([{"text": prompt_text}], GEMINI_MODEL_GENERATOR, response_schema=QUESTION_SCHEMA))
    except Exception as e:
        return None


def evaluate_all_answers_batch(batch_data, evaluation_pattern, lang_code='en'):
    prompt_text = f"You are an expert Test Evaluator evaluating multiple student answers. Evaluation Pattern: {evaluation_pattern} Data: {json.dumps(batch_data, indent=2)} Provide the evaluation results as a JSON ARRAY containing one object for EVERY question in the batch."
    try:
        raw_text = call_gemini_api([{"text": prompt_text}], GEMINI_MODEL_EVALUATOR,
                                   response_schema=BATCH_EVALUATION_SCHEMA)
        results = json.loads(clean_gemini_json_output(raw_text))
        return {res['question_id']: res for res in results}
    except Exception as e:
        return {item['question_id']: {"score_awarded": 0, "feedback_html": f"Error: {e}", "deductions": []} for item in
                batch_data}


def generate_smart_flashcards(topic, document_text=None, num_cards=10):
    prompt = f"You are an expert tutor. Create EXACTLY {num_cards} flashcards. "
    if document_text: prompt += f"Base them strictly on this text: {document_text[:4000]} "
    if topic: prompt += f"Focus on the topic: {topic}. "
    prompt += "Provide a JSON array of objects with 'front' (The short Concept/Term) and 'back' (The Definition/Explanation)."
    try:
        raw_text = call_gemini_api([{"text": prompt}], GEMINI_MODEL_GENERATOR, response_schema=FLASHCARD_SCHEMA)
        return json.loads(clean_gemini_json_output(raw_text))
    except Exception as e:
        print(f"Flashcard Error: {e}")
        return None


# ==========================================
# === AUTHENTICATION ROUTES ===
# ==========================================
@app.route('/register', methods=['GET', 'POST'])
def register_page():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')

        # Security: Do not allow registering as an admin via the public form!
        if role == 'admin':
            flash("You cannot register as an admin.", 'error')
            return redirect(url_for('register_page'))

        try:
            if User.query.filter_by(username=username).first():
                flash('Username already exists. Please choose another.', 'error')
                return redirect(url_for('register_page'))
            new_user = User(username=username, password=generate_password_hash(password), role=role)
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            if role == 'teacher':
                return redirect(url_for('dashboard_page'))
            else:
                return redirect(url_for('student_dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'System Error. Details: {str(e)}', 'error')
            return redirect(url_for('register_page'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            if user.role != role:
                flash(f'Error: This username belongs to a {user.role}, not a {role}.', 'error')
                return redirect(url_for('login_page'))

            login_user(user)
            if user.role == 'teacher':
                return redirect(url_for('dashboard_page'))
            elif user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('student_dashboard'))
        else:
            flash('Invalid credentials. Please try again.', 'error')
    return render_template('login.html')


# NEW DEDICATED ADMIN LOGIN ROUTE
@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # Only look for admin users
        user = User.query.filter_by(username=username, role='admin').first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid Admin Credentials.', 'error')

    return render_template('admin_login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('landing_page'))


@app.route('/', methods=['GET', 'POST'])
def landing_page():
    global current_language
    if request.method == 'POST':
        session['lang'] = request.form.get('language', 'en')
        current_language = session['lang']
        return redirect(url_for('landing_page'))
    current_language = session.get('lang', 'en')
    if current_user.is_authenticated:
        if current_user.role == 'teacher':
            return redirect(url_for('dashboard_page'))
        elif current_user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('student_dashboard'))
    return render_template('landing.html', lang_strings=LANG_STRINGS[current_language], current_lang=current_language)


# ==========================================
# === ADMIN ROUTES ===
# ==========================================
@app.route('/admin')
@login_required
def admin_redirect():
    if current_user.role != 'admin': return redirect(url_for('landing_page'))
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if current_user.role != 'admin': return redirect(url_for('landing_page'))
    total_teachers = User.query.filter_by(role='teacher').count()
    total_students = User.query.filter_by(role='student').count()
    total_tests = TestSession.query.filter_by(is_practice=False).count()
    total_submissions = TestSubmission.query.count()
    return render_template('admin_dashboard.html',
                           teachers=total_teachers, students=total_students,
                           tests=total_tests, submissions=total_submissions,
                           username=current_user.username)


@app.route('/admin/users')
@login_required
def admin_users():
    if current_user.role != 'admin': return redirect(url_for('landing_page'))
    users = User.query.filter(User.role != 'admin').all()
    return render_template('admin_users.html', users=users, username=current_user.username)


@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if current_user.role != 'admin': return redirect(url_for('landing_page'))
    user_to_delete = db.session.get(User, user_id)
    if user_to_delete and user_to_delete.role != 'admin':
        db.session.delete(user_to_delete)
        db.session.commit()
        flash(f'User {user_to_delete.username} has been deleted successfully.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/tests')
@login_required
def admin_tests():
    if current_user.role != 'admin': return redirect(url_for('landing_page'))
    tests = db.session.query(
        TestSession,
        User.username.label('author_name'),
        func.count(TestSubmission.id).label('sub_count')
    ).join(User, TestSession.author_id == User.id) \
        .outerjoin(TestSubmission, TestSession.id == TestSubmission.test_id) \
        .filter(TestSession.is_practice == False) \
        .group_by(TestSession.id).all()

    return render_template('admin_tests.html', tests=tests, username=current_user.username)


# ==========================================
# === STUDENT DASHBOARD & API ROUTES ===
# ==========================================
@app.route('/student_dashboard', methods=['GET', 'POST'])
@login_required
def student_dashboard():
    if current_user.role != 'student': return redirect(url_for('landing_page'))

    if request.method == 'POST':
        test_id = request.form.get('test_id', '').strip()
        test_session = db.session.get(TestSession, test_id)
        if test_session and not test_session.is_practice:
            sub_id = generate_unique_id()
            new_sub = TestSubmission(id=sub_id, test_id=test_id, student_id=current_user.id)
            db.session.add(new_sub)
            db.session.commit()
            session['submission_id'] = sub_id
            session['current_question_index'] = 0
            return redirect(url_for('test_page'))
        flash('Invalid Test ID or Test does not exist.', 'error')

    submissions = TestSubmission.query.filter_by(student_id=current_user.id).order_by(
        TestSubmission.created_at.desc()).all()
    evaluated_subs = [s for s in submissions if s.is_evaluated]
    evaluated_subs.reverse()

    chart_labels = [sub.created_at.strftime('%b %d') for sub in evaluated_subs]
    chart_scores = [
        round((sub.overall_score / sub.total_possible_score * 100), 2) if sub.total_possible_score > 0 else 0 for sub in
        evaluated_subs]
    flashcard_sets = FlashcardSet.query.filter_by(student_id=current_user.id).order_by(
        FlashcardSet.created_at.desc()).all()

    return render_template('student_dashboard.html', submissions=submissions, username=current_user.username,
                           chart_labels=json.dumps(chart_labels), chart_scores=json.dumps(chart_scores),
                           flashcards=flashcard_sets)


@app.route('/api/ask_doubt', methods=['POST'])
@login_required
def ask_doubt():
    if current_user.role != 'student': return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json(silent=True) or {}
    question = data.get('question')
    if not question: return jsonify({'answer': 'Please ask a valid question.'})
    prompt = f"A student asked the following doubt: '{question}'. Explain the concept simply. Provide a clear explanation, an easy-to-understand example, and step-by-step logic if applicable. Format your response beautifully in Markdown."
    try:
        return jsonify({'answer': call_gemini_api([{"text": prompt}], GEMINI_MODEL_GENERATOR)})
    except Exception as e:
        return jsonify(
            {'answer': "⚠️ Sorry, I couldn't process that right now. **Please ensure your Gemini API Key is valid.**"})


@app.route('/api/study_advisor', methods=['GET'])
@login_required
def study_advisor():
    if current_user.role != 'student': return jsonify({'error': 'Unauthorized'}), 403
    try:
        weak_answers = db.session.query(StudentAnswer, Question) \
            .join(Question, StudentAnswer.question_id == Question.id) \
            .join(TestSubmission, StudentAnswer.submission_id == TestSubmission.id) \
            .filter(TestSubmission.student_id == current_user.id) \
            .filter(StudentAnswer.score_awarded < (Question.marks * 0.5)).limit(5).all()

        if not weak_answers: return jsonify({
                                                'advice': "### 🎉 Excellent Work!\n\nYou are scoring highly across the board. Keep practicing your current topics to maintain your knowledge."})
        topics = [q.question_text for a, q in weak_answers]
        prompt = f"A student got these questions wrong recently: {topics}. Act as an encouraging AI tutor. Write 3 short, actionable bullet points explaining what fundamental concepts they should study next to improve. Use Markdown formatting."
        return jsonify({'advice': call_gemini_api([{"text": prompt}], GEMINI_MODEL_GENERATOR)})
    except Exception:
        return jsonify({
                           'advice': "⚠️ Could not analyze past tests. **Please verify your API key** and ensure you have completed at least one test."})


@app.route('/flashcards_setup', methods=['GET', 'POST'])
@login_required
def flashcards_setup():
    if current_user.role != 'student': return redirect(url_for('landing_page'))
    if request.method == 'POST':
        topic = request.form.get('topic')
        num_cards = int(request.form.get('num_cards', 10))
        doc_text = ""

        if 'syllabus_pdf' in request.files and allowed_file(request.files['syllabus_pdf'].filename):
            file = request.files['syllabus_pdf']
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(filepath)
            doc_text = extract_text_from_pdf(filepath)
            os.remove(filepath)

        if not topic and not doc_text:
            flash("Please provide a topic or upload a syllabus PDF.", "error")
            return redirect(url_for('flashcards_setup'))

        cards_data = generate_smart_flashcards(topic, doc_text, num_cards)
        if cards_data:
            set_id = generate_unique_id()
            new_set = FlashcardSet(id=set_id, student_id=current_user.id, topic=topic or "From Syllabus PDF")
            db.session.add(new_set)
            for c in cards_data: db.session.add(
                Flashcard(set_id=set_id, front_concept=c['front'], back_definition=c['back']))
            db.session.commit()
            return redirect(url_for('flashcards_view', set_id=set_id))
        else:
            flash("⚠️ AI failed to generate flashcards. Please verify your Gemini API key.", "error")

    return render_template('flashcards_setup.html')


@app.route('/flashcards_view/<set_id>')
@login_required
def flashcards_view(set_id):
    if current_user.role != 'student': return redirect(url_for('landing_page'))
    flashcard_set = db.session.get(FlashcardSet, set_id)
    if not flashcard_set or flashcard_set.student_id != current_user.id: return redirect(url_for('student_dashboard'))
    return render_template('flashcards_view.html', fset=flashcard_set)


@app.route('/practice', methods=['GET', 'POST'])
@login_required
def practice_mode():
    if current_user.role != 'student': return redirect(url_for('landing_page'))
    if request.method == 'POST':
        try:
            params = {'subject': request.form.get('subject'), 'topic': request.form.get('topic'),
                      'num_questions': int(request.form.get('num_questions', 5)), 'question_type': 'objective',
                      'question_hardness': request.form.get('difficulty'), 'generation_method': 'practice_mode'}
            questions = generate_test_questions(params)
            if questions:
                test_id = generate_unique_id()
                save_test_to_db(test_id, current_user.id, params, questions, 'positive', is_practice=True)
                sub_id = generate_unique_id()
                db.session.add(TestSubmission(id=sub_id, test_id=test_id, student_id=current_user.id))
                db.session.commit()
                session['submission_id'] = sub_id
                session['current_question_index'] = 0
                return redirect(url_for('test_page'))
            else:
                flash('⚠️ Failed to generate practice test. Please check your API Key in the console.', 'error')
        except Exception as e:
            flash(f'System Error: {str(e)}', 'error')
    return render_template('practice.html')


@app.route('/adaptive', methods=['GET', 'POST'])
@login_required
def adaptive_setup():
    if current_user.role != 'student': return redirect(url_for('landing_page'))
    if request.method == 'POST':
        try:
            subject, topic, max_q = request.form.get('subject'), request.form.get('topic'), int(
                request.form.get('num_questions', 5))
            start_diff = 'medium'
            new_q_data = generate_single_adaptive_question(subject, topic, start_diff, "None")
            if new_q_data:
                test_id = generate_unique_id()
                new_test = TestSession(id=test_id, author_id=current_user.id, is_practice=True,
                                       generation_method='adaptive', evaluation_pattern='positive',
                                       test_params=json.dumps(
                                           {'subject': subject, 'topic': topic, 'generation_method': 'adaptive'}))
                db.session.add(new_test)
                q = new_q_data[0]
                db.session.add(
                    Question(id=q['question_id'], test_id=test_id, order_num=0, question_text=q['question_text'],
                             question_type=q['question_type'], options=json.dumps(q.get('options', [])),
                             correct_answer=json.dumps(q.get('correct_answer', '')),
                             grading_rubric=json.dumps(q.get('grading_rubric', [])), marks=float(q.get('marks', 0))))
                sub_id = generate_unique_id()
                db.session.add(TestSubmission(id=sub_id, test_id=test_id, student_id=current_user.id))
                db.session.commit()
                session['submission_id'], session['current_question_index'] = sub_id, 0
                session['adaptive_subject'], session['adaptive_topic'] = subject, topic
                session['adaptive_diff'], session['adaptive_max_q'], session['adaptive_curr_q'] = start_diff, max_q, 1
                return redirect(url_for('test_page'))
            else:
                flash('⚠️ Failed to start adaptive test. Please verify your Gemini API Key.', 'error')
        except Exception as e:
            flash(f'System Error: {str(e)}', 'error')
    return render_template('adaptive.html')


# ==========================================
# === TEACHER ROUTES ===
# ==========================================
@app.route('/dashboard')
@login_required
def dashboard_page():
    if current_user.role != 'teacher': return redirect(url_for('landing_page'))
    current_language = session.get('lang', 'en')
    all_tests = TestSession.query.filter_by(author_id=current_user.id, is_practice=False).order_by(
        TestSession.created_at.desc()).all()
    return render_template('dashboard.html', lang_strings=LANG_STRINGS[current_language], current_lang=current_language,
                           tests=all_tests, username=current_user.username)


@app.route('/delete_test/<test_id>', methods=['POST'])
@login_required
def delete_test(test_id):
    if current_user.role != 'teacher': return redirect(url_for('landing_page'))
    test_session = db.session.get(TestSession, test_id)
    if test_session and test_session.author_id == current_user.id:
        db.session.delete(test_session)
        db.session.commit()
    return redirect(url_for('dashboard_page'))


@app.route('/dashboard/<test_id>')
@login_required
def test_analytics_page(test_id):
    if current_user.role not in ['teacher', 'admin']:
        return redirect(url_for('landing_page'))

    current_language = session.get('lang', 'en')
    test_session = db.session.get(TestSession, test_id)

    if not test_session or (test_session.author_id != current_user.id and current_user.role != 'admin'):
        return redirect(url_for('dashboard_page') if current_user.role == 'teacher' else url_for('admin_dashboard'))

    submissions = TestSubmission.query.filter_by(test_id=test_id).all()
    total_score = sum([sub.overall_score for sub in submissions if sub.is_evaluated])
    evaluated_count = sum([1 for sub in submissions if sub.is_evaluated])

    max_possible = 0
    for sub in submissions:
        if sub.is_evaluated and sub.total_possible_score > 0:
            max_possible = sub.total_possible_score
            break
    if max_possible == 0 and test_session.questions:
        max_possible = sum([q.marks for q in test_session.questions])

    avg_score = round(total_score / evaluated_count, 2) if evaluated_count > 0 else 0

    # Safely load params from the database
    try:
        params = json.loads(test_session.test_params)
    except:
        params = {}

    # ⚡️ FIX: Added params=params to the render_template below!
    return render_template('test_analytics.html',
                           lang_strings=LANG_STRINGS.get(current_language, LANG_STRINGS['en']),
                           current_lang=current_language,
                           test=test_session,
                           submissions=submissions,
                           avg_score=avg_score,
                           max_possible=max_possible,
                           total_students=len(submissions),
                           params=params,
                           json=json)


@app.route('/update_submission_score', methods=['POST'])
@login_required
def update_submission_score():
    if current_user.role != 'teacher': return jsonify({'success': False}), 403
    data = request.json
    submission = db.session.get(TestSubmission, data.get('sub_id'))
    if submission and submission.test.author_id == current_user.id:
        submission.overall_score = float(data.get('new_score'))
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False}), 400


@app.route('/question_bank')
@login_required
def question_bank():
    if current_user.role != 'teacher': return redirect(url_for('landing_page'))
    current_language = session.get('lang', 'en')
    tests = TestSession.query.filter_by(author_id=current_user.id, is_practice=False).all()
    bank_questions = []
    for t in tests:
        try:
            params = json.loads(t.test_params)
            subject, topic, difficulty = params.get('subject', 'General'), params.get('topic', 'General'), params.get(
                'question_hardness', 'Medium')
        except:
            subject, topic, difficulty = "General", "General", "Medium"
        for q in t.questions:
            q.subject = subject;
            q.topic = topic;
            q.difficulty = difficulty
            bank_questions.append(q)
    return render_template('question_bank.html', questions=bank_questions, lang_strings=LANG_STRINGS[current_language],
                           current_lang=current_language, username=current_user.username)


@app.route('/create_from_bank', methods=['POST'])
@login_required
def create_from_bank():
    if current_user.role != 'teacher': return redirect(url_for('landing_page'))
    selected_q_ids = request.form.getlist('selected_questions')
    if not selected_q_ids: return redirect(url_for('question_bank'))

    new_test_id = generate_unique_id()
    new_test = TestSession(id=new_test_id, author_id=current_user.id, generation_method='question_bank',
                           evaluation_pattern='positive', test_params=json.dumps(
            {"subject": "Custom (From Bank)", "topic": "Mixed", "question_hardness": "Mixed"}))
    db.session.add(new_test)
    for idx, q_id in enumerate(selected_q_ids):
        orig_q = db.session.get(Question, q_id)
        if orig_q and orig_q.test.author_id == current_user.id:
            new_q = Question(id=generate_unique_id(), test_id=new_test_id, order_num=idx,
                             question_text=orig_q.question_text, question_type=orig_q.question_type,
                             options=orig_q.options, correct_answer=orig_q.correct_answer,
                             grading_rubric=orig_q.grading_rubric, marks=orig_q.marks)
            db.session.add(new_q)
    db.session.commit()
    return redirect(url_for('test_ready_page', test_id=new_test_id))


@app.route('/teacher', methods=['GET', 'POST'])
@login_required
def teacher_page():
    if current_user.role != 'teacher': return redirect(url_for('landing_page'))
    global current_language
    if request.method == 'POST':
        session['lang'] = request.form.get('language', 'en')
        current_language = session['lang']
        gen_method, eval_pattern = request.form.get('generation_method'), request.form.get('evaluation_pattern')
        session['form_params'] = request.form.to_dict()

        if gen_method == 'pdf_upload':
            if 'test_paper' in request.files and allowed_file(request.files['test_paper'].filename):
                file = request.files['test_paper']
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
                file.save(filepath)
                document_text = extract_text_from_pdf(filepath)
                os.remove(filepath)
                if document_text:
                    session['document_text'] = document_text
                    session['evaluation_pattern'] = eval_pattern
                    return redirect(url_for('pdf_options_page'))
        elif gen_method == 'default':
            params = session['form_params']
            params['generation_method'] = 'default'
            questions = generate_test_questions(params, session['lang'])
            if questions:
                test_id = generate_unique_id()
                save_test_to_db(test_id, current_user.id, params, questions, eval_pattern)
                return redirect(url_for('test_ready_page', test_id=test_id))
        elif gen_method == 'custom_prompt':
            questions = generate_questions_from_custom_prompt(request.form.get('custom_prompt'), session['lang'])
            if questions:
                test_id = generate_unique_id()
                save_test_to_db(test_id, current_user.id, {'generation_method': 'custom_prompt'}, questions,
                                eval_pattern)
                return redirect(url_for('test_ready_page', test_id=test_id))
    return render_template('index.html', lang_strings=LANG_STRINGS[current_language], current_lang=current_language,
                           params=session.get('form_params', {}),
                           generation_method=session.get('generation_method', 'default'),
                           username=current_user.username)


@app.route('/test_ready/<test_id>')
@login_required
def test_ready_page(test_id):
    if current_user.role != 'teacher': return redirect(url_for('landing_page'))
    current_language = session.get('lang', 'en')
    test_session = db.session.get(TestSession, test_id)
    if not test_session or test_session.author_id != current_user.id: return redirect(url_for('dashboard_page'))
    return render_template('test_ready.html', lang_strings=LANG_STRINGS[current_language],
                           current_lang=current_language, test_id=test_id)


@app.route('/pdf_options', methods=['GET', 'POST'])
@login_required
def pdf_options_page():
    if current_user.role != 'teacher': return redirect(url_for('landing_page'))
    global current_language
    current_language = session.get('lang', 'en')
    if not session.get('document_text'): return redirect(url_for('teacher_page'))
    if request.method == 'POST':
        eval_pattern, num_qs, test_mode = request.form.get('evaluation_pattern'), int(
            request.form.get('num_questions_for_pdf', 5)), request.form.get('test_mode_choice')
        params = {'generation_method': 'pdf_upload'}
        questions = generate_test_questions(params, session['lang'], document_text=session['document_text'],
                                            num_questions_from_pdf=num_qs)
        if questions:
            test_id = generate_unique_id()
            if test_mode == 'generate_questions_only': params['generation_method'] = 'pdf_upload_questions_only'
            save_test_to_db(test_id, current_user.id, params, questions, eval_pattern)
            if test_mode == 'generate_questions_only':
                sub_id = generate_unique_id()
                db.session.add(TestSubmission(id=sub_id, test_id=test_id, student_id=current_user.id))
                db.session.commit()
                session['submission_id'] = sub_id
                return redirect(url_for('evaluate_results'))
            return redirect(url_for('test_ready_page', test_id=test_id))
    return render_template('pdf_options.html', lang_strings=LANG_STRINGS[current_language],
                           current_lang=current_language, params=session.get('form_params', {}))


# ==========================================
# === EXPORT & DOWNLOAD ROUTES ===
# ==========================================
@app.route('/export_csv/<test_id>')
@login_required
def export_csv(test_id):
    test_session = db.session.get(TestSession, test_id)
    if not test_session or (test_session.author_id != current_user.id and current_user.role != 'admin'):
        return redirect(url_for('dashboard_page'))

    submissions = TestSubmission.query.filter_by(test_id=test_id).all()
    questions = Question.query.filter_by(test_id=test_id).order_by(Question.order_num).all()
    si = io.StringIO()
    writer = csv.writer(si)

    header = ['Student_Name', 'Date_Taken', 'Status', 'Overall_Score', 'Total_Possible']
    for q in questions:
        header.append(f"Q{q.order_num + 1}_Answer")
        header.append(f"Q{q.order_num + 1}_Score")
    writer.writerow(header)

    for sub in submissions:
        # ⚡️ FIXED: Changed sub.student to sub.student_ref
        student_name = sub.student_ref.username if sub.student_ref else "Anonymous"

        row = [student_name, sub.created_at.strftime('%Y-%m-%d %H:%M'), 'Evaluated' if sub.is_evaluated else 'Pending',
               sub.overall_score if sub.is_evaluated else '-', sub.total_possible_score if sub.is_evaluated else '-']
        ans_records = {a.question_id: a for a in StudentAnswer.query.filter_by(submission_id=sub.id).all()}
        for q in questions:
            ans = ans_records.get(q.id)
            row.append(ans.student_answer_text if ans else 'No Answer')
            row.append(ans.score_awarded if ans else 0)
        writer.writerow(row)

    output = si.getvalue()
    headers = {"Content-Disposition": f"attachment; filename=test_grades_{test_id[:8]}.csv"}
    return Response(output, mimetype='text/csv', headers=headers)


@app.route('/print_test/<test_id>')
@login_required
def print_test(test_id):
    test_session = db.session.get(TestSession, test_id)
    if not test_session or test_session.author_id != current_user.id: return redirect(url_for('dashboard_page'))
    questions = Question.query.filter_by(test_id=test_id).order_by(Question.order_num).all()
    test_params = json.loads(test_session.test_params)
    for q in questions:
        if q.options and q.options != 'null':
            try:
                raw_opts = json.loads(q.options)
                q.parsed_options = raw_opts if isinstance(raw_opts, list) else list(raw_opts.values()) if isinstance(
                    raw_opts, dict) else []
            except:
                q.parsed_options = []
        else:
            q.parsed_options = []
    return render_template('print_test.html', test=test_session, questions=questions, params=test_params)


# ==========================================
# === TEST TAKING & EVALUATION ROUTES ===
# ==========================================
@app.route('/test', methods=['GET', 'POST'])
@login_required
def test_page():
    global current_language
    current_language = session.get('lang', 'en')
    sub_id = session.get('submission_id')
    submission = db.session.get(TestSubmission, sub_id) if sub_id else None
    if not submission or submission.student_id != current_user.id: return redirect(url_for('landing_page'))

    questions = Question.query.filter_by(test_id=submission.test_id).order_by(Question.order_num).all()
    q_index = session.get('current_question_index', 0)
    test_session = db.session.get(TestSession, submission.test_id)
    is_adaptive = test_session.generation_method == 'adaptive'

    if request.method == 'POST':
        current_q = questions[q_index]
        ans_text = ""

        if 'answer_file_upload' in request.files and request.files['answer_file_upload'].filename != '':
            file = request.files['answer_file_upload']
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(filepath)
            ans_text = filepath
        else:
            q_type_lower = current_q.question_type.lower().strip() if current_q.question_type else 'subjective'
            parsed_options = []
            if current_q.options and current_q.options != 'null':
                try:
                    parsed_options = json.loads(current_q.options) if isinstance(json.loads(current_q.options),
                                                                                 list) else list(
                        json.loads(current_q.options).values())
                except:
                    pass

            is_objective_ui = q_type_lower in ['objective', 'mcq'] or (
                        len(parsed_options) > 0 and 'multiple' not in q_type_lower)
            is_multiple_ui = 'multiple' in q_type_lower

            if is_objective_ui or is_multiple_ui:
                ans_data = request.form.getlist(f'answer_{current_q.id}')
                ans_text = ans_data[0] if is_objective_ui and ans_data else json.dumps(ans_data) if ans_data else '[]'
            else:
                ans_text = request.form.get(f'answer_text_{current_q.id}') or ''

        ans_record = StudentAnswer.query.filter_by(submission_id=sub_id, question_id=current_q.id).first()
        if not ans_record:
            ans_record = StudentAnswer(submission_id=sub_id, question_id=current_q.id)
            db.session.add(ans_record)
        ans_record.student_answer_text = ans_text
        db.session.commit()

        if is_adaptive:
            batch_data = [
                {"question_id": current_q.id, "question_text": current_q.question_text, "total_marks": current_q.marks,
                 "ideal_answer": current_q.correct_answer, "grading_rubric": current_q.grading_rubric,
                 "student_submission": ans_text or "No Answer"}]
            eval_results = evaluate_all_answers_batch(batch_data, test_session.evaluation_pattern,
                                                      session.get('lang', 'en'))
            res = eval_results.get(current_q.id, {})

            ans_record.score_awarded, ans_record.feedback_html, ans_record.deductions, ans_record.processed_answer = res.get(
                'score_awarded', 0), res.get('feedback_html', ''), json.dumps(res.get('deductions', [])), ans_text
            db.session.commit()

            score_percent = ans_record.score_awarded / current_q.marks if current_q.marks > 0 else 0
            diff_levels = ['basic', 'medium', 'hard', 'scholar', 'master']
            curr_diff_idx = diff_levels.index(session.get('adaptive_diff', 'medium')) if session.get('adaptive_diff',
                                                                                                     'medium') in diff_levels else 1

            if score_percent >= 0.8 and curr_diff_idx < len(diff_levels) - 1:
                curr_diff_idx += 1
            elif score_percent <= 0.4 and curr_diff_idx > 0:
                curr_diff_idx -= 1

            new_diff = diff_levels[curr_diff_idx]
            session['adaptive_diff'] = new_diff
            max_q, curr_q_num = session.get('adaptive_max_q', 5), session.get('adaptive_curr_q', 1)

            if curr_q_num < max_q:
                past_qs_text = " | ".join([q.question_text for q in questions])
                new_q_data = generate_single_adaptive_question(session.get('adaptive_subject'),
                                                               session.get('adaptive_topic'), new_diff, past_qs_text)
                if new_q_data:
                    q = new_q_data[0]
                    db.session.add(Question(id=q['question_id'], test_id=submission.test_id, order_num=curr_q_num,
                                            question_text=q['question_text'], question_type=q['question_type'],
                                            options=json.dumps(q.get('options', [])),
                                            correct_answer=json.dumps(q.get('correct_answer', '')),
                                            grading_rubric=json.dumps(q.get('grading_rubric', [])),
                                            marks=float(q.get('marks', 0))))
                    db.session.commit()
                session['adaptive_curr_q'] += 1
                session['current_question_index'] = curr_q_num
                return redirect(url_for('test_page'))
            else:
                submission.is_evaluated, submission.overall_score, submission.total_possible_score = True, sum(
                    a.score_awarded for a in StudentAnswer.query.filter_by(submission_id=sub_id).all()), sum(
                    q.marks for q in Question.query.filter_by(test_id=submission.test_id).all())
                db.session.commit()
                return redirect(url_for('evaluate_results'))
        else:
            if q_index + 1 < len(questions):
                session['current_question_index'] = q_index + 1
                return redirect(url_for('test_page'))
            return redirect(url_for('evaluate_results'))

    if q_index < len(questions):
        current_q = questions[q_index]
        ans_record = StudentAnswer.query.filter_by(submission_id=sub_id, question_id=current_q.id).first()
        student_ans = ans_record.student_answer_text if ans_record else ""

        parsed_options = []
        if current_q.options and current_q.options != 'null':
            try:
                parsed_options = json.loads(current_q.options) if isinstance(json.loads(current_q.options),
                                                                             list) else list(
                    json.loads(current_q.options).values())
            except:
                pass

        q_dict = {'question_id': current_q.id, 'question_text': current_q.question_text, 'question_type': str(
            current_q.question_type).lower().strip() if current_q.question_type else 'subjective',
                  'marks': current_q.marks, 'options': parsed_options}
        uploaded_filename = student_ans.split('/')[-1] if student_ans and (
                    student_ans.startswith('uploads/') or student_ans.endswith(('.pdf', '.jpg', '.png'))) else None

        return render_template('test.html', lang_strings=LANG_STRINGS[current_language], current_lang=current_language,
                               question=q_dict, question_index=q_index + 1,
                               total_questions=session.get('adaptive_max_q', len(questions)) if is_adaptive else len(
                                   questions), student_answer=student_ans, uploaded_filename=uploaded_filename)

    return redirect(url_for('evaluate_results'))


@app.route('/test_evaluation/<sub_id>', methods=['GET'])
@login_required
def view_student_evaluation(sub_id):
    global current_language
    current_language = session.get('lang', 'en')

    submission = db.session.get(TestSubmission, sub_id)
    if not submission:
        flash('Submission not found.', 'error')
        return redirect(url_for('dashboard_page') if current_user.role == 'teacher' else url_for('student_dashboard'))

    test_session = db.session.get(TestSession, submission.test_id)
    if not test_session: return redirect(url_for('landing_page'))

    # ⚡️ FIX: Check Permissions (Admin, Teacher who made the test, OR Student who took it)
    is_admin = current_user.role == 'admin'
    is_author = current_user.role == 'teacher' and test_session.author_id == current_user.id
    is_owner = current_user.role == 'student' and submission.student_id == current_user.id

    if not (is_admin or is_author or is_owner):
        return redirect(url_for('landing_page'))

    questions = Question.query.filter_by(test_id=test_session.id).order_by(Question.order_num).all()
    try: params = json.loads(test_session.test_params)
    except: params = {}

    is_questions_only = params.get('generation_method') == 'pdf_upload_questions_only'

    evaluation_results = []
    for q in questions:
        ans_record = StudentAnswer.query.filter_by(submission_id=sub_id, question_id=q.id).first()
        evaluation_results.append({
            "question_id": q.id, "question_text": q.question_text,
            "student_answer": ans_record.student_answer_text if ans_record else "",
            "ideal_answer": q.correct_answer, "possible": q.marks,
            "score": ans_record.score_awarded if ans_record else 0,
            "feedback": ans_record.feedback_html if ans_record else get_string('generate_questions_only_feedback') if is_questions_only else "",
            "deductions": json.loads(ans_record.deductions) if ans_record and ans_record.deductions else []
        })

    return render_template('results.html', lang_strings=LANG_STRINGS.get(current_language, LANG_STRINGS['en']), current_lang=current_language, evaluation_results=evaluation_results, overall_score=submission.overall_score, total_possible_score=submission.total_possible_score, test_data={'parameters': params}, is_questions_only_mode=is_questions_only)

@app.route('/evaluate_results', methods=['GET'])
@login_required
def evaluate_results():
    global current_language
    current_language = session.get('lang', 'en')

    sub_id = session.get('submission_id')
    submission = db.session.get(TestSubmission, sub_id) if sub_id else None
    if not submission: return redirect(url_for('landing_page'))

    test_session = db.session.get(TestSession, submission.test_id)
    questions = Question.query.filter_by(test_id=test_session.id).order_by(Question.order_num).all()
    try: params = json.loads(test_session.test_params)
    except: params = {}
    is_questions_only = params.get('generation_method') == 'pdf_upload_questions_only'

    if not submission.is_evaluated and not is_questions_only:
        batch_data = []
        for q in questions:
            ans_record = StudentAnswer.query.filter_by(submission_id=sub_id, question_id=q.id).first()
            raw_text = ans_record.student_answer_text if ans_record else ""

            if raw_text and (raw_text.endswith('.pdf') or raw_text.endswith(('.jpg', '.png'))):
                if raw_text.endswith('.pdf'): raw_text = extract_text_from_pdf(raw_text)
                else: raw_text = extract_text_from_image(raw_text, "image/png" if raw_text.endswith('.png') else "image/jpeg")

            if ans_record:
                ans_record.processed_answer = raw_text
                db.session.commit()

            batch_data.append({"question_id": q.id, "question_text": q.question_text, "total_marks": q.marks, "ideal_answer": q.correct_answer, "grading_rubric": q.grading_rubric, "student_submission": raw_text or "No Answer Provided"})

        # ⚡️ FIX IS HERE: Changed session['lang'] to session.get('lang', 'en')
        eval_results = evaluate_all_answers_batch(batch_data, test_session.evaluation_pattern, session.get('lang', 'en'))

        total_score, total_possible = 0, 0
        for q in questions:
            res = eval_results.get(q.id, {})
            ans_record = StudentAnswer.query.filter_by(submission_id=sub_id, question_id=q.id).first()
            if not ans_record:
                ans_record = StudentAnswer(submission_id=sub_id, question_id=q.id)
                db.session.add(ans_record)

            ans_record.score_awarded, ans_record.feedback_html, ans_record.deductions = res.get('score_awarded', 0), res.get('feedback_html', ''), json.dumps(res.get('deductions', []))
            total_score += ans_record.score_awarded
            total_possible += q.marks

        submission.overall_score, submission.total_possible_score, submission.is_evaluated = total_score, total_possible, True
        db.session.commit()

    evaluation_results = []
    for q in questions:
        ans_record = StudentAnswer.query.filter_by(submission_id=sub_id, question_id=q.id).first()
        evaluation_results.append({
            "question_id": q.id, "question_text": q.question_text,
            "student_answer": ans_record.student_answer_text if ans_record else "",
            "ideal_answer": q.correct_answer, "possible": q.marks,
            "score": ans_record.score_awarded if ans_record else 0,
            "feedback": ans_record.feedback_html if ans_record else get_string('generate_questions_only_feedback') if is_questions_only else "",
            "deductions": json.loads(ans_record.deductions) if ans_record and ans_record.deductions else []
        })

    session.pop('submission_id', None)
    session.pop('current_question_index', None)
    return render_template('results.html', lang_strings=LANG_STRINGS[current_language], current_lang=current_language,
                           evaluation_results=evaluation_results, overall_score=submission.overall_score,
                           total_possible_score=submission.total_possible_score, test_data={'parameters': params},
                           is_questions_only_mode=is_questions_only)


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # ALWAYS ENSURE 'test'/'test' ADMIN ACCOUNT EXISTS
        if not User.query.filter_by(username='test', role='admin').first():
            admin_user = User(username='test', password=generate_password_hash('test'), role='admin')
            db.session.add(admin_user)
            db.session.commit()
            print("✅ Default Admin account ready (Username: test, Password: test)")
        print("✅ Database successfully initialized with Security Tables!")
    app.run(debug=True)
