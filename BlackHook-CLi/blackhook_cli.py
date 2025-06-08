#!/usr/bin/env python3
"""
Webhook Capture CLI - A tool to capture and inspect webhook requests using Textual
"""

import asyncio
import json
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import click
import requests
from flask import Flask, request
from pyngrok import ngrok
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static, Button, Label
from textual.reactive import reactive
from textual.message import Message
from textual.screen import Screen
from textual.binding import Binding

class WebhookCapture:
    def __init__(self):
        self.app = Flask(__name__)
        self.requests_log: List[Dict] = []
        self.ngrok_tunnel = None
        self.server_thread = None
        self.running = False
        self.port = 8080
        self.callbacks = []
        
        # Setup Flask routes
        self.app.add_url_rule('/', 'catch_all', self.catch_request, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
        self.app.add_url_rule('/<path:path>', 'catch_all_path', self.catch_request, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
        
        # Disable Flask logging
        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)

    def add_callback(self, callback):
        """Add callback to be called when new request arrives"""
        self.callbacks.append(callback)

    def catch_request(self, path=''):
        """Capture incoming webhook requests"""
        req_data = {
            'id': len(self.requests_log) + 1,
            'timestamp': datetime.now(),
            'method': request.method,
            'path': f"/{path}" if path else "/",
            'headers': dict(request.headers),
            'query_params': dict(request.args),
            'body': None,
            'content_type': request.content_type or 'unknown',
            'remote_addr': request.remote_addr
        }
        
        # Capture body based on content type
        try:
            if request.is_json:
                req_data['body'] = request.get_json()
            elif request.content_type and 'form' in request.content_type:
                req_data['body'] = dict(request.form)
            else:
                body_text = request.get_data(as_text=True)
                if body_text:
                    try:
                        req_data['body'] = json.loads(body_text)
                    except:
                        req_data['body'] = body_text
        except Exception as e:
            req_data['body'] = f"Error reading body: {str(e)}"
        
        self.requests_log.append(req_data)
        
        # Notify callbacks
        for callback in self.callbacks:
            try:
                callback(req_data)
            except:
                pass
        
        return {"status": "received", "id": req_data['id']}, 200

    def start_server(self):
        """Start the Flask server in a separate thread"""
        self.server_thread = threading.Thread(
            target=lambda: self.app.run(host='0.0.0.0', port=self.port, debug=False, use_reloader=False),
            daemon=True
        )
        self.server_thread.start()
        time.sleep(1)  # Give server time to start

    def create_ngrok_tunnel(self):
        """Create ngrok tunnel"""
        try:
            self.ngrok_tunnel = ngrok.connect(self.port)
            return self.ngrok_tunnel.public_url
        except Exception as e:
            return None

    def kill_tunnel(self):
        """Kill the ngrok tunnel"""
        if self.ngrok_tunnel:
            ngrok.disconnect(self.ngrok_tunnel.public_url)
            self.ngrok_tunnel = None
            return True
        return False

    def format_body(self, body) -> str:
        """Format request body for display"""
        if body is None:
            return "No body"
        
        if isinstance(body, dict):
            formatted = []
            for k, v in body.items():
                if isinstance(v, (dict, list)):
                    formatted.append(f"{k}: {json.dumps(v, indent=2)}")
                else:
                    formatted.append(f"{k}: {v}")
            return "\n".join(formatted)
        elif isinstance(body, list):
            return json.dumps(body, indent=2)
        else:
            return str(body)

    def get_request_by_id(self, req_id: int):
        """Get request by ID"""
        return next((r for r in self.requests_log if r['id'] == req_id), None)

    def clear_requests(self):
        """Clear all requests"""
        self.requests_log.clear()

    def export_requests(self, output_file: str):
        """Export requests to JSON file"""
        if not self.requests_log:
            return False, "No requests to export"
        
        # Convert datetime objects to strings for JSON serialization
        export_data = []
        for req in self.requests_log:
            req_copy = req.copy()
            req_copy['timestamp'] = req_copy['timestamp'].isoformat()
            export_data.append(req_copy)
        
        try:
            with open(output_file, 'w') as f:
                json.dump(export_data, f, indent=2)
            return True, f"Exported {len(export_data)} requests"
        except Exception as e:
            return False, f"Error exporting: {e}"

class RequestDetailScreen(Screen):
    def __init__(self, request_data: Dict):
        super().__init__()
        self.request_data = request_data
    
    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static(f"Request ID: {self.request_data['id']}", id="title"),
            Static(f"Timestamp: {self.request_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}", id="timestamp"),
            Static(f"Method: {self.request_data['method']}", id="method"),
            Static(f"Path: {self.request_data['path']}", id="path"),
            Static(f"Remote Address: {self.request_data['remote_addr']}", id="remote"),
            Static(f"Content-Type: {self.request_data['content_type']}", id="content_type"),
            Static("Headers:", id="headers_title"),
            Static("\n".join([f"{k}: {v}" for k, v in self.request_data['headers'].items()]), id="headers_content"),
            Static("Query Parameters:", id="query_title"),
            Static("\n".join([f"{k}: {v}" for k, v in self.request_data['query_params'].items()]) if self.request_data['query_params'] else "No query parameters", id="query_content"),
            Static("Request Body:", id="body_title"),
            Static(webhook_capture.format_body(self.request_data['body']), id="body_content"),
            Button("Back", id="back_button"),
            id="detail_container"
        )
        yield Footer()
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back_button":
            self.app.pop_screen()

class WebhookApp(App):
    CSS = """
    #title {
        color: cyan;
        text-style: bold;
    }
    
    #status_info {
        color: green;
        margin: 1;
    }
    
    #tunnel_url {
        color: blue;
        text-style: bold;
        margin: 1;
    }
    
    #request_count {
        color: yellow;
        margin: 1;
    }
    
    DataTable {
        height: 20;
    }
    
    #detail_container {
        padding: 1;
    }
    
    #headers_title, #query_title, #body_title {
        text-style: bold;
        color: cyan;
        margin-top: 1;
    }
    
    #headers_content, #query_content, #body_content {
        margin-left: 2;
        margin-bottom: 1;
    }
    
    Button {
        margin: 1;
    }
    """
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("c", "clear", "Clear"),
        Binding("e", "export", "Export"),
        Binding("k", "kill_tunnel", "Kill Tunnel"),
        Binding("enter", "show_detail", "Show Detail"),
    ]
    
    def __init__(self):
        super().__init__()
        self.request_count = reactive(0)
        
    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("Webhook Capture CLI", id="title"),
            Static("Status: Starting...", id="status_info"),
            Static("Tunnel URL: Not created", id="tunnel_url"),
            Static("Requests captured: 0", id="request_count"),
            DataTable(id="requests_table"),
            Horizontal(
                Button("Refresh", id="refresh_btn"),
                Button("Clear", id="clear_btn"),
                Button("Export", id="export_btn"),
                Button("Kill Tunnel", id="kill_btn"),
                Button("Quit", id="quit_btn"),
            ),
            id="main_container"
        )
        yield Footer()
    
    def on_mount(self) -> None:
        """Setup the data table and start monitoring"""
        table = self.query_one("#requests_table", DataTable)
        table.add_columns("ID", "Time", "Method", "Path", "Content-Type", "Body Preview")
        
        # Add callback to update table when new requests arrive
        webhook_capture.add_callback(self.on_new_request)
        
        # Start the refresh timer
        self.set_interval(1.0, self.refresh_table)
        
        # Update status
        self.update_status()
    
    def on_new_request(self, request_data: Dict):
        """Called when a new request arrives"""
        self.request_count += 1
        # The table will be updated by the refresh timer
    
    def refresh_table(self):
        """Refresh the requests table"""
        table = self.query_one("#requests_table", DataTable)
        table.clear()
        
        for req in webhook_capture.requests_log[-50:]:  # Show last 50 requests
            time_str = req['timestamp'].strftime("%H:%M:%S")
            
            # Create body preview
            body_preview = ""
            if req['body']:
                if isinstance(req['body'], dict):
                    keys = list(req['body'].keys())[:3]
                    body_preview = f"Keys: {', '.join(keys)}"
                    if len(req['body']) > 3:
                        body_preview += "..."
                elif isinstance(req['body'], str):
                    body_preview = req['body'][:30] + "..." if len(req['body']) > 30 else req['body']
                else:
                    body_preview = str(req['body'])[:30]
            else:
                body_preview = "empty"
            
            table.add_row(
                str(req['id']),
                time_str,
                req['method'],
                req['path'][:15] + "..." if len(req['path']) > 15 else req['path'],
                req['content_type'][:12] + "..." if len(req['content_type']) > 12 else req['content_type'],
                body_preview
            )
        
        # Update request count
        count_widget = self.query_one("#request_count", Static)
        count_widget.update(f"Requests captured: {len(webhook_capture.requests_log)}")
    
    def update_status(self):
        """Update status information"""
        status_widget = self.query_one("#status_info", Static)
        tunnel_widget = self.query_one("#tunnel_url", Static)
        
        if webhook_capture.ngrok_tunnel:
            status_widget.update("Status: Running")
            tunnel_widget.update(f"Tunnel URL: {webhook_capture.ngrok_tunnel.public_url}")
        else:
            status_widget.update("Status: No tunnel")
            tunnel_widget.update("Tunnel URL: Not created")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh_btn":
            self.action_refresh()
        elif event.button.id == "clear_btn":
            self.action_clear()
        elif event.button.id == "export_btn":
            self.action_export()
        elif event.button.id == "kill_btn":
            self.action_kill_tunnel()
        elif event.button.id == "quit_btn":
            self.action_quit()
    
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection to show request details"""
        table = self.query_one("#requests_table", DataTable)
        row_key = event.row_key
        if row_key is not None:
            row_data = table.get_row(row_key)
            if row_data:
                req_id = int(row_data[0])
                request_data = webhook_capture.get_request_by_id(req_id)
                if request_data:
                    self.push_screen(RequestDetailScreen(request_data))
    
    def action_refresh(self) -> None:
        """Refresh the table"""
        self.refresh_table()
    
    def action_clear(self) -> None:
        """Clear all requests"""
        webhook_capture.clear_requests()
        self.refresh_table()
    
    def action_export(self) -> None:
        """Export requests to JSON"""
        filename = f"webhook_requests_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        success, message = webhook_capture.export_requests(filename)
        # You could show a notification here
    
    def action_kill_tunnel(self) -> None:
        """Kill the ngrok tunnel"""
        webhook_capture.kill_tunnel()
        self.update_status()
    
    def action_show_detail(self) -> None:
        """Show detail of selected request"""
        table = self.query_one("#requests_table", DataTable)
        if table.cursor_row is not None:
            row_data = table.get_row_at(table.cursor_row)
            if row_data:
                req_id = int(row_data[0])
                request_data = webhook_capture.get_request_by_id(req_id)
                if request_data:
                    self.push_screen(RequestDetailScreen(request_data))

webhook_capture = WebhookCapture()

@click.group()
def cli():
    """Webhook Capture CLI - Capture and inspect webhook requests"""
    pass

@cli.command()
@click.option('--port', '-p', default=8080, help='Local port to run server on')
def start(port):
    """Start the webhook capture server with ngrok tunnel and real-time UI"""
    webhook_capture.port = port
    
    print("Starting webhook capture server...")
    
    # Start local server
    webhook_capture.start_server()
    print(f"✓ Local server started on port {port}")
    
    # Create ngrok tunnel
    print("Creating ngrok tunnel...")
    tunnel_url = webhook_capture.create_ngrok_tunnel()
    
    if tunnel_url:
        print(f"✓ Tunnel created: {tunnel_url}")
        print(f"\nSend webhook requests to: {tunnel_url}")
        print("Starting real-time UI...")
        
        # Start the Textual app
        app = WebhookApp()
        app.run()
    else:
        print("Failed to create ngrok tunnel")

@cli.command()
def kill():
    """Kill the ngrok tunnel"""
    if webhook_capture.kill_tunnel():
        print("✓ Tunnel killed successfully")
    else:
        print("No active tunnel found")

@cli.command()
def status():
    """Show current status and active tunnel"""
    if webhook_capture.ngrok_tunnel:
        print(f"✓ Active tunnel: {webhook_capture.ngrok_tunnel.public_url}")
        print(f"✓ Local server: http://localhost:{webhook_capture.port}")
        print(f"Requests captured: {len(webhook_capture.requests_log)}")
    else:
        print("No active tunnel")

@cli.command()
@click.option('--limit', '-l', default=20, help='Limit number of requests shown')
def requests(limit):
    """Show captured requests in a table"""
    if not webhook_capture.requests_log:
        print("No requests captured yet")
        return
    
    print(f"\nLast {min(limit, len(webhook_capture.requests_log))} requests:")
    print("-" * 80)
    print(f"{'ID':<4} {'Time':<10} {'Method':<8} {'Path':<20} {'Content-Type':<15}")
    print("-" * 80)
    
    for req in webhook_capture.requests_log[-limit:]:
        time_str = req['timestamp'].strftime("%H:%M:%S")
        path = req['path'][:18] + "..." if len(req['path']) > 18 else req['path']
        content_type = req['content_type'][:13] + "..." if len(req['content_type']) > 13 else req['content_type']
        
        print(f"{req['id']:<4} {time_str:<10} {req['method']:<8} {path:<20} {content_type:<15}")

@cli.command()
@click.argument('request_id', type=int)
def show(request_id):
    """Show detailed view of a specific request"""
    req = webhook_capture.get_request_by_id(request_id)
    if not req:
        print(f"Request with ID {request_id} not found")
        return
    
    print(f"\nRequest Details - ID: {request_id}")
    print(f"Timestamp: {req['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Method: {req['method']}")
    print(f"Path: {req['path']}")
    print(f"Remote Address: {req['remote_addr']}")
    print(f"Content-Type: {req['content_type']}")
    
    print(f"\nHeaders:")
    for k, v in req['headers'].items():
        print(f"  {k}: {v}")
    
    print(f"\nQuery Parameters:")
    if req['query_params']:
        for k, v in req['query_params'].items():
            print(f"  {k}: {v}")
    else:
        print("  No query parameters")
    
    print(f"\nRequest Body:")
    body_content = webhook_capture.format_body(req['body'])
    for line in body_content.split('\n'):
        print(f"  {line}")

@cli.command()
def clear():
    """Clear all captured requests"""
    webhook_capture.clear_requests()
    print("✓ All requests cleared")

@cli.command()
@click.option('--output', '-o', help='Output file path (JSON format)')
def export(output):
    """Export captured requests to JSON file"""
    output_file = output or f"webhook_requests_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    success, message = webhook_capture.export_requests(output_file)
    
    if success:
        print(f"✓ {message} to {output_file}")
    else:
        print(f"Error: {message}")

@cli.command()
def ui():
    """Start the Textual UI without creating a tunnel (for viewing existing data)"""
    app = WebhookApp()
    app.run()

if __name__ == '__main__':
    cli()
