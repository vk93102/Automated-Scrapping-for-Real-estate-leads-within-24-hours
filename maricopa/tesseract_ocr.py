#!/usr/bin/env python3
"""
Tesseract OCR module for real PDF text extraction
Provides high-quality OCR for property documents
"""

import os
import subprocess
import tempfile
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def ocr_pdf_pages_tesseract(pdf_bytes: bytes, max_pages: int = 8) -> dict:
    """
    Extract text from PDF using Tesseract OCR
    
    Args:
        pdf_bytes: PDF file as bytes
        max_pages: Max pages to OCR (default 8)
    
    Returns:
        {
            'success': bool,
            'text': str (full OCR text),
            'pages': int,
            'error': str (if failed)
        }
    """
    try:
        # Check if Tesseract is installed
        result = subprocess.run(['which', 'tesseract'], capture_output=True, text=True)
        if result.returncode != 0:
            return {
                'success': False,
                'text': '',
                'pages': 0,
                'error': 'Tesseract not installed. Run: brew install tesseract'
            }
        
        import io
        from PIL import Image
        
        # Try using pdf2image if available
        try:
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(pdf_bytes, first_page=1, last_page=max_pages, dpi=150)
        except ImportError:
            # Fallback: try using ImageMagick
            try:
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as pdf_file:
                    pdf_file.write(pdf_bytes)
                    temp_pdf = pdf_file.name
                
                with tempfile.TemporaryDirectory() as temp_dir:
                    # Convert pages to images using ImageMagick
                    img_prefix = os.path.join(temp_dir, 'page')
                    convert_cmd = [
                        'convert',
                        '-density', '150',
                        '-quality', '85',
                        f'{temp_pdf}[0-{max_pages-1}]',
                        f'{img_prefix}.png'
                    ]
                    
                    result = subprocess.run(convert_cmd, capture_output=True, text=True, timeout=60)
                    
                    if result.returncode != 0:
                        return {
                            'success': False,
                            'text': '',
                            'pages': 0,
                            'error': 'Converting PDF to images failed (install: brew install imagemagick pdf2image)'
                        }
                    
                    images = []
                    for img_path in sorted(Path(temp_dir).glob('page*.png')):
                        images.append(Image.open(img_path))
                
                if os.path.exists(temp_pdf):
                    os.unlink(temp_pdf)
            except:
                return {
                    'success': False,
                    'text': '',
                    'pages': 0,
                    'error': 'Need either pdf2image or ImageMagick. Run: pip install pdf2image && brew install imagemagick'
                }
        
        if not images:
            return {
                'success': False,
                'text': '',
                'pages': 0,
                'error': 'No images generated from PDF'
            }
        
        # OCR each image
        all_text = []
        for img in images:
            try:
                # Convert PIL Image to bytes for Tesseract
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as img_file:
                    img.save(img_file, format='PNG')
                    temp_img = img_file.name
                
                # Run Tesseract
                tess_cmd = [
                    'tesseract',
                    temp_img,
                    'stdout',
                    '-l', 'eng',
                    '--psm', '1'
                ]
                
                tess_result = subprocess.run(
                    tess_cmd,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if tess_result.returncode == 0 and tess_result.stdout.strip():
                    all_text.append(tess_result.stdout)
                
                if os.path.exists(temp_img):
                    os.unlink(temp_img)
            except Exception as e:
                logger.warning(f"Tesseract error on image: {e}")
                continue
        
        if not all_text:
            return {
                'success': False,
                'text': '',
                'pages': len(images),
                'error': 'No text extracted from images'
            }
        
        combined_text = '\n\n---PAGE BREAK---\n\n'.join(all_text)
        
        return {
            'success': True,
            'text': combined_text,
            'pages': len(images),
            'error': None
        }
    
    except Exception as e:
        logger.error(f"OCR error: {e}", exc_info=True)
        return {
            'success': False,
            'text': '',
            'pages': 0,
            'error': str(e)
        }


def validate_ocr_text(text: str) -> dict:
    """
    Validate OCR output quality
    
    Returns:
        {
            'valid': bool,
            'confidence': float (0-1),
            'warnings': [str],
            'issues': [str]
        }
    """
    warnings = []
    issues = []
    
    # Check if text is empty
    if not text or len(text.strip()) < 100:
        issues.append("OCR text too short (< 100 chars)")
    
    # Check for gibberish patterns
    if text.count('~') > len(text) * 0.15:
        warnings.append("High tilde count (possible OCR noise)")
    
    # Check for readable content
    word_count = len(text.split())
    if word_count < 50:
        issues.append(f"Very few words extracted ({word_count})")
    
    # Check confidence by analyzing character distribution
    total_chars = len(text)
    special_chars = sum(1 for c in text if not c.isalnum() and not c.isspace())
    special_ratio = special_chars / total_chars if total_chars > 0 else 0
    
    if special_ratio > 0.4:
        warnings.append(f"High special character ratio ({special_ratio:.1%})")
    
    # Estimate confidence
    confidence = 1.0
    if issues:
        confidence -= 0.3 * len(issues)
    if warnings:
        confidence -= 0.1 * len(warnings)
    
    confidence = max(0.0, min(1.0, confidence))
    
    return {
        'valid': len(issues) == 0,
        'confidence': confidence,
        'warnings': warnings,
        'issues': issues
    }


if __name__ == '__main__':
    # Test mode
    logging.basicConfig(level=logging.INFO)
    print("✓ Tesseract OCR module loaded")
    print("Usage: ocr_pdf_pages_tesseract(pdf_bytes, max_pages=8)")
