from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, jsonify
import os
import sqlite3
import json
import logging
from werkzeug.utils import secure_filename
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from langchain.chains import RetrievalQA
from langchain.chains.conversational_retrieval.base import ConversationalRetrievalChain
from langchain.memory import ConversationBufferMemory
from langchain_ollama import OllamaLLM
from langchain_ollama import OllamaEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import TextLoader, PyPDFLoader, Docx2txtLoader, CSVLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter

import fb_bot

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'txt', 'pdf', 'docx', 'md', 'pptx', 'csv'}

# Database for user memories
MEMORY_DB = 'user_memories.db'

def get_memory_db():
    conn = sqlite3.connect(MEMORY_DB)
    return conn

def init_memory_db():
    conn = get_memory_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_memories (
            user_id TEXT PRIMARY KEY,
            history TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_memory_db()

def save_user_memory(user_id, history):
    conn = get_memory_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO user_memories (user_id, history, updated_at)
        VALUES (?, ?, ?)
    ''', (user_id, json.dumps(history), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def load_user_memory(user_id):
    conn = get_memory_db()
    cursor = conn.cursor()
    cursor.execute('SELECT history FROM user_memories WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return json.loads(result[0])
    return []

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/cms')
def cms():
    files = []
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        for f in os.listdir(app.config['UPLOAD_FOLDER']):
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], f)
            if os.path.isfile(filepath):
                files.append({
                    'name': f,
                    'size': os.path.getsize(filepath),
                    'modified': datetime.fromtimestamp(os.path.getmtime(filepath)).strftime('%Y-%m-%d %H:%M:%S')
                })
    files.sort(key=lambda x: x['modified'], reverse=True)
    return render_template('cms.html', documents=files)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files and 'files[]' not in request.files:
        flash('No file part')
        return redirect(request.url)
    
    # Check if it's a folder upload (multiple files)
    files = request.files.getlist('files[]')
    
    if files and files[0].filename:
        # Folder upload
        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename or "")
                if filename:
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(filepath)
        flash(f'Successfully uploaded {len([f for f in files if f.filename])} files')
        return redirect(url_for('cms'))
    
    # Single file upload (fallback)
    file = request.files.get('file')
    if file.filename == '':
        flash('No selected file')
        return redirect(request.url)
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename or "")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        flash('File successfully uploaded')
        return redirect(url_for('cms'))
    else:
        flash('Allowed file types are txt, pdf, docx, csv')
        return redirect(url_for('cms'))

@app.route('/upload-folder', methods=['POST'])
def upload_folder():
    files = request.files.getlist('files')
    
    if not files or not files[0].filename:
        flash('No files selected')
        return redirect(url_for('cms'))
    
    uploaded_count = 0
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename or "")
            if filename:
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                uploaded_count += 1
    
    flash(f'Successfully uploaded {uploaded_count} files')
    return redirect(url_for('cms'))

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/delete/<name>')
def delete_file(name):
    filename = secure_filename(name)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if os.path.exists(filepath):
        os.remove(filepath)
        flash('File deleted successfully')
    
    return redirect(url_for('cms'))

class RAGSystem:
    def __init__(self, llm_model="qwen2.5:3b", embed_model="bge-m3:latest"):
        self.embeddings = OllamaEmbeddings(model=embed_model)
        self.vector_store = None
        self.llm = OllamaLLM(model=llm_model, temperature=0.1)
        logging.info(f"Initializing RAG system with {llm_model}...")
        self.load_documents()
        self.initialize_chain()
    
    def reload(self):
        logging.info("Reloading knowledge base...")
        self.load_documents()
        self.initialize_chain()
        logging.info("Knowledge base reloaded")
    
    def query(self, message, user_id=None):
        """
        Query the RAG system.
        
        Args:
            message: User message
            user_id: If provided, loads/persists conversation memory in SQLite.
                     If None, uses fresh memory (no persistence) - for web chat.
        """
        if not message:
            return {"error": "No message provided"}, 400
        
        if not self.vector_store:
            return {"response": "No documents available for knowledge base. Please upload documents first."}
        
        try:
            # Load user-specific memory from database (only if user_id provided)
            history = load_user_memory(user_id) if user_id else []
            
            # Create memory with history
            memory = ConversationBufferMemory(
                memory_key="chat_history",
                output_key="answer",
                return_messages=True
            )
            
            # Load history into memory
            for msg in history:
                if msg['type'] == 'human':
                    memory.chat_memory.add_user_message(msg['content'])
                elif msg['type'] == 'ai':
                    memory.chat_memory.add_ai_message(msg['content'])
            
            # Create chain with user memory
            qa_chain = ConversationalRetrievalChain.from_llm(
                llm=self.llm,
                retriever=self.vector_store.as_retriever(),
                memory=memory,
                return_source_documents=True,
                verbose=False
            )
            
            # Query
            result = qa_chain.invoke(message)
            
            # Save updated memory to database (only if user_id provided)
            if user_id:
                updated_history = [
                    {"type": "human", "content": message},
                    {"type": "ai", "content": result.get('answer', str(result))}
                ]
                save_user_memory(user_id, history + updated_history)
            
            logging.info(f"Query processed for user {user_id or 'web'}: {message[:30]}...")
            return {"response": result.get('answer', str(result))}
            
        except Exception as e:
            logging.error(f"Query error: {e}")
            return {"response": f"Error processing your query: {str(e)}"}, 500
    
    def load_documents(self):
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            return []
        
        documents = []
        for root, dirs, files in os.walk(app.config['UPLOAD_FOLDER']):
            for filename in files:
                filepath = os.path.join(root, filename)
                
                try:
                    if filename.endswith('.txt'):
                        loader = TextLoader(filepath)
                        docs = loader.load()
                        documents.extend(docs)
                        logging.info(f"Loaded {filename}")
                    elif filename.endswith('.pdf'):
                        loader = PyPDFLoader(filepath)
                        docs = loader.load()
                        documents.extend(docs)
                        logging.info(f"Loaded {filename}")
                    elif filename.endswith('.docx'):
                        loader = Docx2txtLoader(filepath)
                        docs = loader.load()
                        documents.extend(docs)
                        logging.info(f"Loaded {filename}")
                    elif filename.endswith('.csv'):
                        loader = CSVLoader(filepath)
                        docs = loader.load()
                        documents.extend(docs)
                        logging.info(f"Loaded {filename}")
                except Exception as e:
                    logging.error(f"Error loading {filename}: {e}")
        
        if documents:
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000,
                chunk_overlap=200
            )
            
            docs = text_splitter.split_documents(documents)
            self.vector_store = Chroma.from_documents(docs, self.embeddings)
        
        return documents
    
    def initialize_chain(self):
        # Chain is now created per-query with user memory
        pass

rag_system = RAGSystem()

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.get_json()
    message = data.get('message', "")
    # Web chat doesn't save memory - fresh conversation each time
    return jsonify(rag_system.query(message, user_id=None))

@app.route('/api/messages', methods=['GET'])
def get_messages():
    return jsonify([])

@app.route('/api/reload', methods=['POST'])
def reload_knowledge_base():
    try:
        rag_system.reload()
        return jsonify({"status": "success", "message": "Knowledge base reloaded"})
    except Exception as e:
        logging.error(f"Reload error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# Setup Facebook webhook routes
fb_bot.setup_facebook_routes(app, rag_system)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
