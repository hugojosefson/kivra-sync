#!/usr/bin/python3
# -*- coding: utf-8 -*-

from PIL import Image
import logging
from interaction.base import InteractionProvider
from interaction.summary import completion_message

class LocalInteractionProvider(InteractionProvider):
    """Default interaction provider that displays QR codes locally and reports to console."""
    
    def display_qr_code(self, qr_image_path):
        """
        Display a QR code for BankID authentication.
        
        Args:
            qr_image_path (str): Path to the QR code image file
        """
        # Open the image with the system's default image viewer
        try:
            Image.open(qr_image_path).show()
            print(f"QR code has been saved as: {qr_image_path}")
        except Exception as e:
            logging.error(f"Could not display QR code: {e}")
            print(f"QR code saved as '{qr_image_path}'")
    
    def report_completion(self, stats):
        print(completion_message(stats))

    def report_authentication_success(self):
        """
        Report that BankID authentication was successful and data sync is starting.
        """
        print("BankID authentication successful! Starting data sync...")
