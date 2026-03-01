"""
Facebook Messenger Bot Integration

This module handles Facebook webhook integration for the RAG chatbot.

Usage:
1. Copy .env.example to .env
2. Fill in FB credentials
3. Configure webhook URL in Facebook Developer Console
4. Run app.py
"""

import os
import logging
from flask import request

logger = logging.getLogger(__name__)

# Load from environment variables
FB_VERIFY_TOKEN = os.getenv('FB_VERIFY_TOKEN', '')
FB_PAGE_ACCESS_TOKEN = os.getenv('FB_PAGE_ACCESS_TOKEN', '')
FB_PAGE_ID = os.getenv('FB_PAGE_ID', '')

# Check if Facebook is configured
FB_CONFIGURED = bool(FB_VERIFY_TOKEN and FB_PAGE_ACCESS_TOKEN and FB_PAGE_ID)

if FB_CONFIGURED:
    logger.info("Facebook Messenger bot configured and ready")
else:
    logger.info("Facebook Messenger not configured - set FB credentials in .env to enable")


def send_fb_message(sender_id, message_text):
    """
    Send a message to a Facebook user via Graph API.
    
    Requires FB_PAGE_ACCESS_TOKEN to be set in environment.
    """
    if not FB_CONFIGURED:
        logger.warning("Facebook not configured - message not sent")
        return False
    
    import requests
    
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    
    data = {
        "recipient": {"id": sender_id},
        "message": {"text": message_text}
    }
    
    try:
        response = requests.post(url, json=data, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"Failed to send FB message: {response.text}")
            return False
        
        logger.info(f"Message sent to {sender_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error sending FB message: {e}")
        return False


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
        if not FB_CONFIGURED:
            logger.warning("Facebook webhook called but not configured")
            return "Facebook not configured", 200
        
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
