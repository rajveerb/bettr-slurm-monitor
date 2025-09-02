#!/usr/bin/env python3
"""
Slurm GPU Monitor - Terminal UI using Textual
A proper TUI implementation for better display handling
"""

import subprocess
import re
import sqlite3
import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Header, Footer, DataTable, Static, Label, Button, TabbedContent, TabPane
from textual.binding import Binding
from textual import events
from rich.text import Text
from rich.table import Table

class GPUData:
    """Container for GPU monitoring data"""
    def __init__(self):
        self.nodes = []
        self.allocations = {}
        self.queued_jobs = []
        self.last_update = datetime.now()
        
    def update(self, nodes, allocations, queued_jobs):
        self.nodes = nodes
        self.allocations = allocations
        self.queued_jobs = queued_jobs
        self.last_update = datetime.now()

class SlurmCommands:
    """Slurm command execution"""
    
    @staticmethod
    def get_node_info():
        """Get detailed node information from scontrol"""
        try:
            result = subprocess.run(['scontrol', 'show', 'node', '-d'], 
                                   capture_output=True, text=True, timeout=10)
            
            nodes = []
            current_node = {}
            
            for line in result.stdout.split('\n'):
                if line.startswith('NodeName='):
                    if current_node:
                        nodes.append(current_node)
                    current_node = {'name': line.split()[0].split('=')[1]}
                elif 'State=' in line:
                    match = re.search(r'State=(\S+)', line)
                    if match:
                        current_node['state'] = match.group(1)
                elif 'Gres=gpu:' in line:
                    match = re.search(r'gpu:(\w+):(\d+)', line)
                    if match:
                        current_node['gpu_type'] = match.group(1)
                        current_node['gpu_total'] = int(match.group(2))
                elif 'GresUsed=gpu:' in line:
                    match = re.search(r'gpu:\w+:(\d+)', line)
                    if match:
                        current_node['gpu_used'] = int(match.group(1))
            
            if current_node:
                nodes.append(current_node)
            
            return nodes
        except Exception as e:
            return []
    
    @staticmethod
    def get_job_allocations():
        """Get current job allocations with user information"""
        try:
            result = subprocess.run(['squeue', '-o', '%N|%u|%T|%b|%j|%i'], 
                                   capture_output=True, text=True, timeout=10)
            
            allocations = defaultdict(lambda: {'users': set(), 'jobs': []})
            
            for line in result.stdout.split('\n')[1:]:
                if not line or '|' not in line:
                    continue
                parts = line.split('|')
                if len(parts) >= 5:
                    nodelist = parts[0]
                    user = parts[1]
                    state = parts[2]
                    gres = parts[3]
                    jobname = parts[4]
                    jobid = parts[5] if len(parts) > 5 else 'N/A'
                    
                    if state == 'RUNNING' and 'gpu' in gres:
                        gpu_match = re.search(r'gpu:(\w+:)?(\d+)', gres)
                        if gpu_match:
                            gpu_count = int(gpu_match.group(2))
                            
                            nodes = SlurmCommands.expand_nodelist(nodelist)
                            for node in nodes:
                                allocations[node]['users'].add(user)
                                allocations[node]['jobs'].append({
                                    'user': user,
                                    'job': jobname,
                                    'jobid': jobid,
                                    'gpus': gpu_count
                                })
            
            return allocations
        except Exception as e:
            return {}
    
    @staticmethod
    def get_queued_jobs():
        """Get queued jobs information"""
        try:
            result = subprocess.run(['squeue', '-o', '%u|%T|%b|%j|%i|%Q|%S'], 
                                   capture_output=True, text=True, timeout=10)
            
            queued_jobs = []
            
            for line in result.stdout.split('\n')[1:]:
                if not line or '|' not in line:
                    continue
                parts = line.split('|')
                if len(parts) >= 5:
                    user = parts[0]
                    state = parts[1]
                    gres = parts[2]
                    jobname = parts[3]
                    jobid = parts[4]
                    priority = parts[5] if len(parts) > 5 else 'N/A'
                    start_time = parts[6] if len(parts) > 6 else 'N/A'
                    
                    if state == 'PENDING' and 'gpu' in gres:
                        gpu_match = re.search(r'gpu:(\w+:)?(\d+)', gres)
                        if gpu_match:
                            gpu_type = gpu_match.group(1).rstrip(':') if gpu_match.group(1) else 'any'
                            gpu_count = int(gpu_match.group(2))
                            
                            queued_jobs.append({
                                'user': user,
                                'job': jobname,
                                'jobid': jobid,
                                'gpu_type': gpu_type,
                                'gpu_count': gpu_count,
                                'priority': priority,
                                'estimated_start': start_time
                            })
            
            return queued_jobs
        except Exception as e:
            return []
    
    @staticmethod
    def expand_nodelist(nodelist):
        """Expand Slurm nodelist format to individual nodes"""
        try:
            result = subprocess.run(['scontrol', 'show', 'hostname', nodelist],
                                   capture_output=True, text=True, timeout=5)
            return result.stdout.strip().split('\n')
        except:
            return [nodelist]

class OverviewWidget(Static):
    """Overview page widget"""
    
    def compose(self) -> ComposeResult:
        yield Label("📊 GPU Overview", id="overview-title")
        yield DataTable(id="overview-table")
    
    def update_data(self, gpu_data: GPUData):
        """Update the overview display"""
        table = self.query_one("#overview-table", DataTable)
        table.clear(columns=True)
        
        # Add columns
        table.add_column("GPU Type", key="type")
        table.add_column("Total", key="total")
        table.add_column("Used", key="used")
        table.add_column("Available", key="available")
        table.add_column("Usage %", key="usage")
        table.add_column("Nodes", key="nodes")
        
        # Calculate summary
        gpu_summary = defaultdict(lambda: {
            'total': 0, 'used': 0, 'nodes': 0, 
            'drain_nodes': 0, 'true_available': 0
        })
        
        for node in gpu_data.nodes:
            if 'gpu_type' in node:
                gpu_type = node['gpu_type']
                total = node.get('gpu_total', 0)
                used = node.get('gpu_used', 0)
                state = node.get('state', '')
                
                gpu_summary[gpu_type]['total'] += total
                gpu_summary[gpu_type]['used'] += used
                gpu_summary[gpu_type]['nodes'] += 1
                
                is_healthy = 'DRAIN' not in state and 'DOWN' not in state
                if not is_healthy:
                    gpu_summary[gpu_type]['drain_nodes'] += 1
                else:
                    gpu_summary[gpu_type]['true_available'] += (total - used)
        
        # Add rows
        for gpu_type in sorted(gpu_summary.keys()):
            info = gpu_summary[gpu_type]
            usage_pct = (info['used'] / info['total'] * 100) if info['total'] > 0 else 0
            
            table.add_row(
                gpu_type,
                str(info['total']),
                str(info['used']),
                str(info['true_available']),
                f"{usage_pct:.1f}%",
                f"{info['nodes'] - info['drain_nodes']}/{info['nodes']}"
            )

class NodesWidget(Static):
    """Nodes page widget"""
    
    def compose(self) -> ComposeResult:
        yield Label("🖥️ Node Details", id="nodes-title")
        yield DataTable(id="nodes-table")
    
    def update_data(self, gpu_data: GPUData):
        """Update the nodes display"""
        table = self.query_one("#nodes-table", DataTable)
        table.clear(columns=True)
        
        # Add columns
        table.add_column("Node", key="node")
        table.add_column("State", key="state")
        table.add_column("GPU Type", key="gpu_type")
        table.add_column("Total", key="total")
        table.add_column("Used", key="used")
        table.add_column("Available", key="available")
        table.add_column("Users", key="users")
        
        # Add rows
        for node in gpu_data.nodes:
            if 'gpu_type' in node:
                total = node.get('gpu_total', 0)
                used = node.get('gpu_used', 0)
                available = total - used
                state = node.get('state', '')
                
                users = ', '.join(sorted(gpu_data.allocations.get(node['name'], {}).get('users', [])))
                if not users:
                    users = '-'
                
                table.add_row(
                    node['name'],
                    state,
                    node['gpu_type'],
                    str(total),
                    str(used),
                    str(available),
                    users
                )

class QueueWidget(Static):
    """Queue page widget"""
    
    def compose(self) -> ComposeResult:
        yield Label("📋 Job Queue", id="queue-title")
        yield DataTable(id="queue-summary-table")
        yield DataTable(id="queue-users-table")
    
    def update_data(self, gpu_data: GPUData):
        """Update the queue display"""
        # Summary table
        summary_table = self.query_one("#queue-summary-table", DataTable)
        summary_table.clear(columns=True)
        
        summary_table.add_column("GPU Type", key="type")
        summary_table.add_column("Queued Jobs", key="jobs")
        summary_table.add_column("Total GPUs", key="gpus")
        summary_table.add_column("Unique Users", key="users")
        
        # Aggregate data
        gpu_type_summary = defaultdict(lambda: {'jobs': 0, 'gpus': 0, 'users': set()})
        user_queue_summary = defaultdict(lambda: defaultdict(lambda: {'jobs': 0, 'gpus': 0}))
        
        for job in gpu_data.queued_jobs:
            gpu_type = job['gpu_type']
            user = job['user']
            
            gpu_type_summary[gpu_type]['jobs'] += 1
            gpu_type_summary[gpu_type]['gpus'] += job['gpu_count']
            gpu_type_summary[gpu_type]['users'].add(user)
            
            user_queue_summary[user][gpu_type]['jobs'] += 1
            user_queue_summary[user][gpu_type]['gpus'] += job['gpu_count']
        
        # Add summary rows
        for gpu_type in sorted(gpu_type_summary.keys()):
            info = gpu_type_summary[gpu_type]
            summary_table.add_row(
                gpu_type,
                str(info['jobs']),
                str(info['gpus']),
                str(len(info['users']))
            )
        
        # Users table
        users_table = self.query_one("#queue-users-table", DataTable)
        users_table.clear(columns=True)
        
        users_table.add_column("User", key="user")
        users_table.add_column("GPU Type", key="type")
        users_table.add_column("Jobs", key="jobs")
        users_table.add_column("GPUs", key="gpus")
        
        # Sort users by total GPUs requested
        user_totals = {user: sum(gpu['gpus'] for gpu in gpus.values()) 
                       for user, gpus in user_queue_summary.items()}
        
        for user in sorted(user_totals.keys(), key=lambda u: user_totals[u], reverse=True)[:10]:
            for gpu_type in sorted(user_queue_summary[user].keys()):
                data = user_queue_summary[user][gpu_type]
                users_table.add_row(
                    user,
                    gpu_type,
                    str(data['jobs']),
                    str(data['gpus'])
                )

class SlurmMonitorApp(App):
    """Main TUI application"""
    
    CSS = """
    Screen {
        background: $surface;
    }
    
    #overview-table, #nodes-table, #queue-summary-table, #queue-users-table {
        height: 100%;
        border: solid $primary;
    }
    
    Label {
        padding: 1;
        background: $primary;
        color: $text;
        text-style: bold;
    }
    
    TabbedContent {
        height: 100%;
    }
    """
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("1", "show_tab('overview')", "Overview"),
        Binding("2", "show_tab('nodes')", "Nodes"),
        Binding("3", "show_tab('queue')", "Queue"),
    ]
    
    def __init__(self, db_path: Optional[str] = None, refresh_interval: int = 30):
        super().__init__()
        self.gpu_data = GPUData()
        self.db_path = db_path
        self.db_conn = None
        self.refresh_interval = refresh_interval
        
        if self.db_path:
            self.setup_database()
    
    def setup_database(self):
        """Setup SQLite database for logging"""
        if self.db_path:
            self.db_conn = sqlite3.connect(self.db_path)
            cursor = self.db_conn.cursor()
            
            # Create tables (same as before)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gpu_availability (
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    gpu_type TEXT,
                    total INTEGER,
                    used INTEGER,
                    available INTEGER,
                    true_available INTEGER,
                    nodes_total INTEGER,
                    nodes_healthy INTEGER
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS queue_status (
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    gpu_type TEXT,
                    queued_jobs INTEGER,
                    queued_gpus INTEGER,
                    unique_users INTEGER
                )
            ''')
            
            self.db_conn.commit()
    
    def compose(self) -> ComposeResult:
        """Create child widgets"""
        yield Header(show_clock=True)
        
        with TabbedContent(initial="overview"):
            with TabPane("Overview", id="overview"):
                yield OverviewWidget()
            with TabPane("Nodes", id="nodes"):
                yield NodesWidget()
            with TabPane("Queue", id="queue"):
                yield QueueWidget()
        
        yield Footer()
    
    async def on_mount(self) -> None:
        """Called when app starts"""
        self.refresh_data()
        self.set_interval(self.refresh_interval, self.refresh_data)
    
    def refresh_data(self) -> None:
        """Refresh all data from Slurm"""
        nodes = SlurmCommands.get_node_info()
        allocations = SlurmCommands.get_job_allocations()
        queued_jobs = SlurmCommands.get_queued_jobs()
        
        self.gpu_data.update(nodes, allocations, queued_jobs)
        
        # Update all widgets
        for widget in self.query(OverviewWidget):
            widget.update_data(self.gpu_data)
        for widget in self.query(NodesWidget):
            widget.update_data(self.gpu_data)
        for widget in self.query(QueueWidget):
            widget.update_data(self.gpu_data)
        
        # Log to database if enabled
        if self.db_conn:
            self.log_to_database()
    
    def log_to_database(self):
        """Log current state to database"""
        if not self.db_conn:
            return
        
        cursor = self.db_conn.cursor()
        timestamp = datetime.now()
        
        # Calculate and log GPU availability
        gpu_summary = defaultdict(lambda: {
            'total': 0, 'used': 0, 'true_available': 0, 'nodes': 0, 'drain_nodes': 0
        })
        
        for node in self.gpu_data.nodes:
            if 'gpu_type' in node:
                gpu_type = node['gpu_type']
                total = node.get('gpu_total', 0)
                used = node.get('gpu_used', 0)
                state = node.get('state', '')
                
                gpu_summary[gpu_type]['total'] += total
                gpu_summary[gpu_type]['used'] += used
                gpu_summary[gpu_type]['nodes'] += 1
                
                is_healthy = 'DRAIN' not in state and 'DOWN' not in state
                if not is_healthy:
                    gpu_summary[gpu_type]['drain_nodes'] += 1
                else:
                    gpu_summary[gpu_type]['true_available'] += (total - used)
        
        for gpu_type, info in gpu_summary.items():
            cursor.execute('''
                INSERT INTO gpu_availability 
                (timestamp, gpu_type, total, used, available, true_available, nodes_total, nodes_healthy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                timestamp, gpu_type, 
                info['total'], info['used'], 
                info['total'] - info['used'],
                info['true_available'],
                info['nodes'],
                info['nodes'] - info['drain_nodes']
            ))
        
        self.db_conn.commit()
    
    def action_refresh(self) -> None:
        """Handle refresh action"""
        self.refresh_data()
    
    def action_show_tab(self, tab: str) -> None:
        """Switch to a specific tab"""
        tabbed_content = self.query_one(TabbedContent)
        tabbed_content.active = tab

def main():
    """Entry point for TUI version"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Slurm GPU Monitor - TUI Version')
    parser.add_argument('--db', action='store_true', help='Enable SQLite logging')
    parser.add_argument('--db-path', type=str, default='gpu_monitor.db', help='Database path')
    parser.add_argument('--interval', type=int, default=30, help='Refresh interval in seconds')
    
    args = parser.parse_args()
    
    app = SlurmMonitorApp(
        db_path=args.db_path if args.db else None,
        refresh_interval=args.interval
    )
    app.run()

if __name__ == "__main__":
    main()