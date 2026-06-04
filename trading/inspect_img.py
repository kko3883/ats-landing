#!/usr/bin/env python3
"""Inspect an image to understand what it shows."""
import sys
from PIL import Image
import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else '/Users/kelvinko/.hermes/image_cache/img_2d9d529132c6.jpg'
img = Image.open(path)
arr = np.array(img)
h, w, _ = arr.shape

print(f"Image: {w}x{h}")
print(f"File: {path}")

# Background color
bg = arr[:10, :10, :].mean(axis=(0,1))
print(f"Background (top-left): RGB({bg[0]:.0f},{bg[1]:.0f},{bg[2]:.0f})")

# Color analysis
light = np.all(arr > 200, axis=2).sum() / (h*w) * 100
green = ((arr[:,:,1] > 150) & (arr[:,:,0] < 100) & (arr[:,:,2] < 200)).sum() / (h*w) * 100
red = ((arr[:,:,0] > 150) & (arr[:,:,1] < 100) & (arr[:,:,2] < 100)).sum() / (h*w) * 100
yellow = ((arr[:,:,0] > 200) & (arr[:,:,1] > 200) & (arr[:,:,2] < 100)).sum() / (h*w) * 100
blue = ((arr[:,:,2] > 150) & (arr[:,:,0] < 100) & (arr[:,:,1] < 150)).sum() / (h*w) * 100
white = (np.all(arr > 220, axis=2)).sum() / (h*w) * 100

print(f"\nColor composition:")
print(f"  Light (>200): {light:.2f}%")
print(f"  Green/teal:   {green:.2f}%")
print(f"  Red:          {red:.2f}%")
print(f"  Yellow:       {yellow:.2f}%")
print(f"  Blue:         {blue:.2f}%")
print(f"  White:        {white:.2f}%")

# Sample content at different y positions
print(f"\nRow samples (first 200 pixels):")
for y_pct in [0.02, 0.05, 0.1, 0.25, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]:
    y = int(h * y_pct)
    row = arr[y, :200, :]
    rng = (row.min(), row.max())
    print(f"  y={y} ({y_pct*100:.0f}%): RGB range [{rng[0][0]:.0f},{rng[1][0]:.0f}]")

# Check for text by looking for high-contrast edges
# Text usually has sharp transitions
edges_h = np.abs(np.diff(arr.mean(axis=2), axis=1))
edge_density = (edges_h > 40).sum() / (h * (w-1)) * 100
print(f"\nEdge density (text/detail): {edge_density:.2f}%")
print(f"{'This looks like a detail-rich image (screenshot)' if edge_density > 3 else 'This looks like a simple chart/graphic'}")
