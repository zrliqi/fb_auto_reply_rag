"""
Facebook Messenger Bot Integration

This module handles Facebook webhook integration for the RAG chatbot.
"""

import os
import logging
from flask import request

logger = logging.getLogger(__name__)

# TODO: Load from .env
FB_VERIFY_TOKEN = os.getenv('FB_VERIFY_TOKEN', 'YOUR_VERIFY_TOKEN')
FB_PAGE_ACCESS_TOKEN = os.getenv('FB_PAGE_ACCESS_TOKEN', 'YOUR_ACCESS_TOKEN')
FB_PAGE_ID = os.getenv('FB_PAGE_ID', 'YOUR_PAGE_ID')


def send_fb_message(sender_id, message_text):
    """
    TODO: Implement Facebook Graph API call to send message.
    
    Graph API endpoint: POST https://graph.facebook.com/v18.0/me/messages
    Parameters:
    - access_token: FB_PAGE_ACCESS_TOKEN
    - recipient: {"id": sender_id}
    - message: {"text": message_text}
    
    Example using requests:
    ```python
    import requests
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    data = {
        "recipient": {"id": sender_id},
        "message": {"text": message_text}
    }
    response = requests.post(url, json=data)
    ```
    """
    logger.info(f"[FB SKELETON] Would send to {sender_id}: {message_text}")
    return True


def get_fb_sender_id(payload):
    """Extract sender ID from Facebook webhook payload."""
    try:
        entry = payload.get('entry', [])[0]
        messaging = entry.get('messaging', [])[0]
        return messaging.get('sender', {}).get('id')
    except (IndexError, KeyError):
        return None


def get_fb_message_text(payload):
    """Extract message text from Facebook webhook payload."""
    try:
        entry = payload.get('entry', [])[0]
        messaging = entry.get('messaging', [])[0]
        return messaging.get('message', {}).get('text', '')
    except (IndexError, KeyError):
        return None


def setup_facebook_routes(app, rag_system):
    """
    Setup Facebook webhook routes.
    
    Args:
        app: Flask app instance
        rag_system: RAGSystem instance for answering queries
    """
    
    @app.route('/webhook', methods=['GET'])
    def facebook_verify():
        """Facebook webhook verification endpoint."""
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        if mode == 'subscribe' and token == FB_VERIFY_TOKEN:
            logger.info("Facebook webhook verified")
            return challenge, 200
        else:
            logger.warning("Facebook webhook verification failed")
            return "Verification failed", 403

    @app.route('/webhook', methods=['POST'])
    def facebook_webhook():
        """Facebook webhook to receive messages."""
        payload = request.get_json()
        
        if not payload or 'entry' not in payload:
            return "OK", 200
        
        sender_id = get_fb_sender_id(payload)
        message_text = get_fb_message_text(payload)
        
        if sender_id and message_text:
            logger.info(f"Received from {sender_id}: {message_text}")
            
            # Query RAG system with user-specific memory (persisted)
            response = rag_system.query(message_text, user_id=sender_id)
            reply_text = response.get('response', 'Sorry, I could not process your request.')
            
            # Send reply via Facebook
            send_fb_message(sender_id, reply_text)
        
        return "OK", 200
