#!/usr/bin/env python3
"""
Merge OXI e16 scenes while preserving specific pages.

Usage:
  python3 merge_scenes.py <original_scene> <new_scene> \
    --preserve-pages 0-2 \
    --output <output_file>
"""

import json
import sys
import argparse

def parse_page_range(page_str):
    """Parse page range like '0-2' into list [0, 1, 2]"""
    if '-' in page_str:
        start, end = map(int, page_str.split('-'))
        return list(range(start, end + 1))
    else:
        return [int(page_str)]

def merge_scenes(original_file, new_file, preserve_pages, output_file):
    """
    Merge two e16 scene files, preserving specific pages from the original.
    """
    try:
        with open(original_file, 'r') as f:
            original = json.load(f)
        with open(new_file, 'r') as f:
            new_scene = json.load(f)
    except Exception as e:
        print(f"Error reading files: {e}")
        return False
    
    # Start with the new scene as base
    merged = new_scene.copy()
    
    # Preserve specified pages from original
    if 'pages' in original and 'pages' in merged:
        for page_idx in preserve_pages:
            if page_idx < len(original['pages']):
                # Ensure merged has enough pages
                while len(merged['pages']) <= page_idx:
                    merged['pages'].append({
                        "title": f"Page {page_idx + 1}",
                        "channel": 1,
                        "type": "cc",
                        "encoders": [{"abbr": "", "name": "", "cc": i} for i in range(16)]
                    })
                
                # Preserve the original page
                merged['pages'][page_idx] = original['pages'][page_idx]
                print(f"Preserved page {page_idx}")
    
    # Write merged scene
    try:
        with open(output_file, 'w') as f:
            json.dump(merged, f, indent=2)
        print(f"Merged scene written to {output_file}")
        return True
    except Exception as e:
        print(f"Error writing output: {e}")
        return False

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Merge OXI e16 scenes while preserving specific pages'
    )
    parser.add_argument('original', help='Original scene file')
    parser.add_argument('new', help='New scene file')
    parser.add_argument('--preserve-pages', required=True, 
                       help='Pages to preserve from original (e.g. "0-2" or "1,3,5")')
    parser.add_argument('--output', required=True, help='Output file')
    
    args = parser.parse_args()
    
    # Parse page ranges
    preserve = []
    for part in args.preserve_pages.split(','):
        preserve.extend(parse_page_range(part.strip()))
    
    success = merge_scenes(args.original, args.new, preserve, args.output)
    sys.exit(0 if success else 1)
