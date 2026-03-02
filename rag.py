"""
RAG Engine - Core retrieval and question answering system.
"""

import os
import sqlite3
import json
import logging
from datetime import datetime

from langchain_classic.chains import ConversationalRetrievalChain
from langchain_classic.memory import ConversationBufferMemory
from langchain_ollama import OllamaLLM, OllamaEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import TextLoader, PyPDFLoader, Docx2txtLoader, CSVLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

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
    if not user_id:
        return []
    conn = get_memory_db()
    cursor = conn.cursor()
    cursor.execute('SELECT history FROM user_memories WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return json.loads(result[0])
    return []


class RAGSystem:
    def __init__(self, upload_folder='uploads', llm_model="qwen2.5:3b", embed_model="bge-m3:latest"):
        self.upload_folder = upload_folder
        self.embeddings = OllamaEmbeddings(model=embed_model)
        self.vector_store = None
        self.llm = OllamaLLM(model=llm_model, temperature=0.1)
        logger.info(f"Initializing RAG system with {llm_model}...")
        self.load_documents()
        self.initialize_chain()
    
    def reload(self):
        logger.info("Reloading knowledge base...")
        self.load_documents()
        self.initialize_chain()
        logger.info("Knowledge base reloaded")
    
    def query(self, message, user_id=None):
        """
        Query the RAG system.
        
        Args:
            message: User message
            user_id: If provided, loads/persists conversation memory in SQLite.
                     If None, uses fresh memory (no persistence).
        """
        if not message:
            return {"error": "No message provided"}, 400

        # Load user-specific memory from database (only if user_id provided)
        history = load_user_memory(user_id) if user_id else []

        # Fallback to normal chat if no RAG documents are available.
        if not self.vector_store:
            try:
                prompt_parts = [
                    "You are a helpful, natural conversational assistant.",
                    "Respond like a human assistant in clear, friendly English.",
                ]

                for msg in history[-10:]:
                    if msg.get('type') == 'human':
                        prompt_parts.append(f"User: {msg.get('content', '')}")
                    elif msg.get('type') == 'ai':
                        prompt_parts.append(f"Assistant: {msg.get('content', '')}")

                prompt_parts.append(f"User: {message}")
                prompt_parts.append("Assistant:")
                prompt = "\n".join(prompt_parts)

                answer = str(self.llm.invoke(prompt)).strip()
                if not answer:
                    answer = "I am here. Tell me what you want to talk about."

                if user_id:
                    updated_history = [
                        {"type": "human", "content": message},
                        {"type": "ai", "content": answer}
                    ]
                    save_user_memory(user_id, history + updated_history)

                logger.info(f"Chat fallback used for user {user_id or 'web'}: {message[:30]}...")
                return {"response": answer}
            except Exception as e:
                logger.error(f"Fallback chat error: {e}")
                return {"response": f"Error processing your query: {str(e)}"}, 500

        try:
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
                verbose=True
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
            
            logger.info(f"Query processed for user {user_id or 'web'}: {message[:30]}...")
            return {"response": result.get('answer', str(result))}
            
        except Exception as e:
            logger.error(f"Query error: {e}")
            return {"response": f"Error processing your query: {str(e)}"}, 500
    
    def load_documents(self):
        # Clear existing vector store first
        if self.vector_store:
            try:
                self.vector_store.delete_collection()
            except:
                pass
            self.vector_store = None
        
        if not os.path.exists(self.upload_folder):
            return []
        
        documents = []
        for root, dirs, files in os.walk(self.upload_folder):
            for filename in files:
                filepath = os.path.join(root, filename)
                
                try:
                    if filename.endswith('.txt'):
                        loader = TextLoader(filepath)
                        docs = loader.load()
                        documents.extend(docs)
                        logger.info(f"Loaded {filename}")
                    elif filename.endswith('.pdf'):
                        loader = PyPDFLoader(filepath)
                        docs = loader.load()
                        documents.extend(docs)
                        logger.info(f"Loaded {filename}")
                    elif filename.endswith('.docx'):
                        loader = Docx2txtLoader(filepath)
                        docs = loader.load()
                        documents.extend(docs)
                        logger.info(f"Loaded {filename}")
                    elif filename.endswith('.csv'):
                        loader = CSVLoader(filepath)
                        docs = loader.load()
                        documents.extend(docs)
                        logger.info(f"Loaded {filename}")
                except Exception as e:
                    logger.error(f"Error loading {filename}: {e}")
        
        if documents:
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000,
                chunk_overlap=200
            )
            
            docs = text_splitter.split_documents(documents)
            self.vector_store = Chroma.from_documents(docs, self.embeddings)
        
        return documents
    
    def initialize_chain(self):
        # Chain is created per-query with user memory
        pass
