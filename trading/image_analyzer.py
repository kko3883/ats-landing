#!/usr/bin/env python3
"""Analyze image structure without vision API."""
import sys
from PIL import Image
import numpy as np

path = sys.argv[1]
img = Image.open(path)
arr = np.array(img)
h, w, _ = arr.shape

print(f"Size: {w}x{h}")

# Sample rows for color analysis
bg = arr[:10, :10, :].mean(axis=(0,1)).astype(int)
print(f"Background: RGB({bg[0]},{bg[1]},{bg[2]})")

# Look for text: scan for horizontal strips with high contrast
print("\nContent scan (every 5% height):")
for pct in range(0, 101, 5):
    y = int(h * pct / 100)
    row = arr[y, :, :].astype(float)
    avg = row.mean(axis=0)
    brightness = row.mean()
    contrast = row.std()
    
    # Detect if this row has text (sharp transitions between dark and light)
    edges = np.sum(np.abs(np.diff(row.mean(axis=1))) > 30)
    
    # Detect horizontal lines (uniform rows)
    uniformity = row.std(axis=0).mean()
    
    if edges > 20 or contrast > 40:
        marker = "TEXT/DETAIL" if edges > 20 else ""
        print(f"  {pct:3d}% y={y:4d}  bright={brightness:.0f}  contrast={contrast:.0f}  edges={edges}  {marker}")

# Check for specific patterns (IBKR TWS UI elements)
# Look for blocks of uniform color (buttons, panels)
print("\nColor blocks (regions >100px with uniform color):")
step = 50
for y in range(0, h - step, step):
    for x in range(0, w - step, step):
        block = arr[y:y+step, x:x+step, :]
        std = block.std(axis=(0,1)).mean()
        mean = block.mean(axis=(0,1)).astype(int)
        if std < 8 and mean[0] > 30 and mean[0] < 250:  # uniform, non-border, non-bg
            rgb = f"RGB({mean[0]},{mean[1]},{mean[2]})"
            print(f"  Block at ({x},{y}) {step}x{step}: {rgb} (std={std:.1f})")
