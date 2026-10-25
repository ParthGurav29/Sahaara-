#!/usr/bin/env python3
"""
Create initial caregiver and person users for the demo profile.
Run this after database migration to set up initial accounts.
"""
import sys
sys.path.insert(0, '/Users/parthmangeshgurav/Desktop/sahaara/backend')

from database import init_db, get_user_by_email, create_user
from auth.security import hash_password, generate_user_id

# Initialize database (runs migrations)
init_db()

# Default profile ID
PROFILE_ID = "asha_demo_001"

# Caregiver user (Rohan)
CAREGIVER_EMAIL = "caregiver@example.com"
CAREGIVER_PASSWORD = "demo123"  # Change this!

# Person user (Asha)
PERSON_EMAIL = "asha@example.com"
PERSON_PASSWORD = "demo123"  # Change this!

def create_user_if_not_exists(email: str, password: str, role: str, profile_id: str):
    """Create a user if they don't already exist."""
    existing = get_user_by_email(email)
    if existing:
        print(f"User {email} already exists (user_id: {existing['user_id']})")
        return existing
    
    user_id = generate_user_id()
    password_hash = hash_password(password)
    
    user = create_user(
        user_id=user_id,
        email=email,
        password_hash=password_hash,
        role=role,
        profile_id=profile_id,
    )
    print(f"Created {role} user: {email} (user_id: {user_id})")
    return user

if __name__ == "__main__":
    print("Creating initial users for Sahaara M2...")
    print(f"Profile: {PROFILE_ID}")
    print()
    
    # Create caregiver
    create_user_if_not_exists(CAREGIVER_EMAIL, CAREGIVER_PASSWORD, "caregiver", PROFILE_ID)
    
    # Create person
    create_user_if_not_exists(PERSON_EMAIL, PERSON_PASSWORD, "person", PROFILE_ID)
    
    print()
    print("Done! You can now login with:")
    print(f"  Caregiver: {CAREGIVER_EMAIL} / {CAREGIVER_PASSWORD}")
    print(f"  Person:    {PERSON_EMAIL} / {PERSON_PASSWORD}")
    print()
    print("IMPORTANT: Change these passwords in production!")