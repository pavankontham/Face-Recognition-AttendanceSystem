from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import numpy as np
from PIL import Image
import io
import os
from typing import Optional
import json
from datetime import datetime, timedelta
from supabase import create_client
from dotenv import load_dotenv
import uuid
import base64

# Try to import face recognition, but make it optional for now
try:
    import face_recognition
    import cv2
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False
    print("Face recognition not available. Running in demo mode.")

# Import our enhanced liveness detection
try:
    from liveness_detection import LivenessDetector
    LIVENESS_DETECTION_AVAILABLE = True
    liveness_detector = LivenessDetector()
except ImportError as e:
    LIVENESS_DETECTION_AVAILABLE = False
    liveness_detector = None
    print(f"Enhanced liveness detection not available: {e}. Using basic checks.")

# Load environment variables
load_dotenv()

app = FastAPI(title="Face Recognition API", version="1.0.0")

# CORS middleware - Updated for production deployment
allowed_origins = [
    "http://localhost:3000",  # Development
    "https://localhost:3000",  # Development HTTPS
]

# Add production origins from environment variable
if os.getenv("FRONTEND_URL"):
    allowed_origins.append(os.getenv("FRONTEND_URL"))

# Add common Render frontend patterns
frontend_url_patterns = [
    "https://face-recognition-frontend.onrender.com",
    "https://face-recognition-attendance.onrender.com",
    "https://attendance-system.onrender.com"
]
allowed_origins.extend(frontend_url_patterns)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Supabase client
supabase_url = os.getenv("SUPABASE_URL", "https://qkrusouqwmrpernncabq.supabase.co")
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFrcnVzb3Vxd21ycGVybm5jYWJxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc0OTA0Nzk2OSwiZXhwIjoyMDY0NjIzOTY5fQ.yP8WLKdMDuGZpwrTeU2kYSCUtq6wSG4GsOp4A62CdW0")

# Create Supabase client - now that schema is set up, it should work
try:
    # Create client without options to avoid compatibility issues
    supabase = create_client(supabase_url, supabase_key)
    # Test the connection with a simple query
    test_result = supabase.table("users").select("count", count="exact").execute()
    SUPABASE_AVAILABLE = True
    print("✅ Supabase connected successfully!")
except Exception as e:
    print(f"❌ Supabase connection failed: {e}")
    print("Running in demo mode without database")
    supabase = None
    SUPABASE_AVAILABLE = False

# Create directories
os.makedirs("temp", exist_ok=True)
os.makedirs("encodings", exist_ok=True)

def load_image_from_upload(file: UploadFile) -> np.ndarray:
    """Load image from uploaded file"""
    try:
        # Read the uploaded file
        contents = file.file.read()
        
        # Convert to PIL Image
        pil_image = Image.open(io.BytesIO(contents))
        
        # Convert to RGB if necessary
        if pil_image.mode != 'RGB':
            pil_image = pil_image.convert('RGB')
        
        # Convert to numpy array
        image_array = np.array(pil_image)
        
        return image_array
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error loading image: {str(e)}")

def enhanced_liveness_check(image: np.ndarray) -> dict:
    """Enhanced liveness detection using MediaPipe and comprehensive analysis"""
    if not FACE_RECOGNITION_AVAILABLE:
        return {
            "passed": True,
            "confidence": 0.95,
            "message": "Liveness check skipped (demo mode)"
        }

    # Use enhanced liveness detection if available
    if LIVENESS_DETECTION_AVAILABLE and liveness_detector:
        try:
            results = liveness_detector.comprehensive_liveness_check(image)
            return {
                "passed": results['is_live'],
                "confidence": results['confidence'],
                "message": results['message'],
                "details": results['checks']
            }
        except Exception as e:
            print(f"Enhanced liveness detection error: {e}")
            # Fall back to basic check

    # Fallback to basic liveness check
    try:
        # Convert to grayscale for analysis
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

        # Skip blur detection for better user experience - only check basic requirements
        # laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        # Blur check disabled to improve user experience

        # Check if face is detected
        face_locations = face_recognition.face_locations(image)
        if len(face_locations) != 1:
            return {
                "passed": False,
                "confidence": 0.1,
                "message": "Please ensure only one face is visible in the camera."
            }

        # Basic size check
        top, right, bottom, left = face_locations[0]
        face_height = bottom - top
        face_width = right - left

        if face_height < 100 or face_width < 100:
            return {
                "passed": False,
                "confidence": 0.3,
                "message": "Face too small. Please move closer to the camera."
            }

        # Check face area contrast
        face_region = gray[top:bottom, left:right]
        if face_region.size > 0:
            contrast = np.std(face_region)
            if contrast < 20:
                return {
                    "passed": False,
                    "confidence": 0.4,
                    "message": "Low image contrast. This might be a photo. Please ensure you're a real person."
                }

        # Check brightness - more lenient thresholds
        brightness = np.mean(gray)
        if brightness < 20 or brightness > 240:
            return {
                "passed": False,
                "confidence": 0.3,
                "message": "Extreme lighting conditions. Please adjust lighting."
            }

        return {
            "passed": True,
            "confidence": 0.8,
            "message": "Basic liveness check passed"
        }
    except Exception as e:
        print(f"Liveness check error: {e}")
        return {
            "passed": True,
            "confidence": 0.5,
            "message": "Liveness check completed with warnings"
        }

@app.get("/")
async def root():
    return {"message": "Face Recognition API is running"}

@app.post("/enroll")
async def enroll_face(
    image: UploadFile = File(...),
    user_id: str = Form(...)
):
    """Enroll a user's face"""
    try:
        # Load image
        image_array = load_image_from_upload(image)

        # Enhanced liveness check
        liveness_result = enhanced_liveness_check(image_array)
        if not liveness_result["passed"]:
            return {
                "success": False,
                "message": liveness_result["message"],
                "liveness_details": liveness_result.get("details", {})
            }

        if not FACE_RECOGNITION_AVAILABLE:
            # Demo mode - return success with dummy encoding
            encoding_list = [0.0] * 128  # Dummy 128-dimensional encoding
        else:
            # Get face encodings
            face_encodings = face_recognition.face_encodings(image_array)

            if len(face_encodings) == 0:
                return {
                    "success": False,
                    "message": "No face detected in the image. Please try again."
                }

            if len(face_encodings) > 1:
                return {
                    "success": False,
                    "message": "Multiple faces detected. Please ensure only one face is visible."
                }

            # Get the face encoding
            face_encoding = face_encodings[0]

            # Convert to list for JSON serialization and ensure it's a proper numpy array
            encoding_list = np.array(face_encoding).tolist()

        # Save to Supabase
        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "message": "Face enrolled successfully (demo mode)",
                "user_id": user_id
            }

        try:
            # First, get the user's database ID and role from Firebase ID
            user_result = supabase.table("users").select("id, role").eq("firebase_id", user_id).execute()

            if not user_result.data:
                return {
                    "success": False,
                    "message": "User not found. Please ensure you are registered."
                }

            user_data = user_result.data[0]
            db_user_id = user_data["id"]
            user_role = user_data["role"]

            # Check if user is a student (only students can enroll faces)
            if user_role != "student":
                return {
                    "success": False,
                    "message": "Face enrollment is only available for students."
                }

            # Save the enrolled image
            try:
                # Reset file pointer to beginning
                image.file.seek(0)
                contents = image.file.read()
                image_base64 = base64.b64encode(contents).decode('utf-8')

                # Create data URL - this will be used for both profile photo and enrolled image
                content_type = image.content_type or 'image/jpeg'
                enrolled_image_url = f"data:{content_type};base64,{image_base64}"

                # Save face encoding with database user ID and enrolled image
                supabase.table("face_encodings").upsert({
                    "user_id": db_user_id,
                    "encoding": encoding_list,
                    "enrolled_image_url": enrolled_image_url
                }).execute()

                # Use the same enrolled image as profile photo
                supabase.table("users").update({
                    "profile_photo_url": enrolled_image_url
                }).eq("firebase_id", user_id).execute()

            except Exception as photo_error:
                print(f"Warning: Could not save enrolled image: {photo_error}")
                # Still save the encoding without the image
                supabase.table("face_encodings").upsert({
                    "user_id": db_user_id,
                    "encoding": encoding_list
                }).execute()

            return {
                "success": True,
                "message": "Face enrolled successfully",
                "user_id": user_id
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Database error: {str(e)}"
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Enrollment failed: {str(e)}")

@app.post("/recognize")
async def recognize_face(
    image: UploadFile = File(...),
    user_id: str = Form(...)
):
    """Recognize a user's face for attendance"""
    try:
        print(f"🔍 Face recognition request received for user: {user_id}")

        # Load image
        image_array = load_image_from_upload(image)
        print(f"✅ Image loaded successfully, shape: {image_array.shape}")
        
        # Enhanced liveness check
        print("🔍 Starting liveness check...")
        liveness_result = enhanced_liveness_check(image_array)
        print(f"✅ Liveness check completed: {liveness_result}")

        if not liveness_result["passed"]:
            print(f"❌ Liveness check failed: {liveness_result['message']}")
            return {
                "success": True,
                "recognized": False,
                "liveness_check": False,
                "confidence": liveness_result["confidence"],
                "message": liveness_result["message"],
                "liveness_details": liveness_result.get("details", {})
            }
        
        if not FACE_RECOGNITION_AVAILABLE:
            # Demo mode - always recognize successfully
            return {
                "success": True,
                "recognized": True,
                "liveness_check": True,
                "confidence": 0.95,
                "message": "Face recognized successfully (demo mode)"
            }

        # Get face encodings from the image
        print("🔍 Extracting face encodings...")
        face_encodings = face_recognition.face_encodings(image_array)
        print(f"✅ Found {len(face_encodings)} face encoding(s)")

        if len(face_encodings) == 0:
            print("❌ No face detected in image")
            return {
                "success": True,
                "recognized": False,
                "liveness_check": True,
                "message": "No face detected in the image."
            }

        if len(face_encodings) > 1:
            print(f"❌ Multiple faces detected: {len(face_encodings)}")
            return {
                "success": True,
                "recognized": False,
                "liveness_check": True,
                "message": "Multiple faces detected. Please ensure only one face is visible."
            }

        # Get the face encoding
        unknown_encoding = face_encodings[0]
        
        # Get stored encoding from database
        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "recognized": True,
                "liveness_check": True,
                "confidence": 0.95,
                "message": "Face recognized successfully (demo mode)"
            }

        try:
            # First, get the user's database ID from Firebase ID
            print(f"🔍 Looking up user in database: {user_id}")
            user_result = supabase.table("users").select("id").eq("firebase_id", user_id).execute()

            if not user_result.data:
                print("❌ User not found in database")
                return {
                    "success": True,
                    "recognized": False,
                    "liveness_check": True,
                    "message": "User not found. Please ensure you are registered."
                }

            db_user_id = user_result.data[0]["id"]
            print(f"✅ Found user with database ID: {db_user_id}")

            # Get face encoding using database user ID
            print("🔍 Looking up enrolled face encoding...")
            result = supabase.table("face_encodings").select("encoding").eq("user_id", db_user_id).execute()

            if not result.data:
                print("❌ No enrolled face found")
                return {
                    "success": True,
                    "recognized": False,
                    "liveness_check": True,
                    "message": "No enrolled face found. Please enroll your face first."
                }

            # Convert stored encoding back to numpy array
            print("✅ Found enrolled face encoding, converting to numpy array...")
            stored_encoding = np.array(result.data[0]["encoding"])
            print(f"✅ Stored encoding shape: {stored_encoding.shape}")

            # Compare faces
            print("🔍 Comparing faces...")
            matches = face_recognition.compare_faces([stored_encoding], unknown_encoding, tolerance=0.6)
            face_distances = face_recognition.face_distance([stored_encoding], unknown_encoding)

            print(f"✅ Face comparison completed - Match: {matches[0]}, Distance: {face_distances[0]}")

            if matches[0]:
                confidence = 1 - face_distances[0]
                print(f"🎉 Face recognized successfully! Confidence: {confidence}")
                return {
                    "success": True,
                    "recognized": True,
                    "liveness_check": True,
                    "confidence": float(confidence),
                    "message": "Face recognized successfully"
                }
            else:
                print(f"❌ Face not recognized. Distance: {face_distances[0]}")
                return {
                    "success": True,
                    "recognized": False,
                    "liveness_check": True,
                    "confidence": float(1 - face_distances[0]),
                    "message": "Face not recognized"
                }

        except Exception as e:
            return {
                "success": False,
                "message": f"Database error: {str(e)}"
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recognition failed: {str(e)}")

@app.post("/api/users")
async def create_user(user_data: dict):
    """Create a new user in the database"""
    if not SUPABASE_AVAILABLE:
        return {
            "success": False,
            "message": "Database not available. Please set up Supabase connection."
        }

    try:
        result = supabase.table("users").insert(user_data).execute()
        return {
            "success": True,
            "message": "User created successfully",
            "data": result.data[0] if result.data else None
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Database error: {str(e)}"
        }

@app.get("/api/users/{firebase_id}")
async def get_user_by_firebase_id(firebase_id: str):
    """Get user by Firebase ID"""
    if not SUPABASE_AVAILABLE:
        return {
            "success": False,
            "message": "Database not available. Please set up Supabase connection."
        }

    try:
        result = supabase.table("users").select("*").eq("firebase_id", firebase_id).execute()
        if result.data:
            return {
                "success": True,
                "message": "User retrieved successfully",
                "data": result.data[0]
            }
        else:
            return {
                "success": False,
                "message": "User not found"
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"Database error: {str(e)}"
        }

@app.get("/api/face-encodings/{firebase_id}")
async def get_face_encoding(firebase_id: str):
    """Get face encoding status for a user"""
    if not SUPABASE_AVAILABLE:
        return {
            "success": False,
            "message": "Database not available"
        }

    try:
        # Get user's database ID
        user_result = supabase.table("users").select("id").eq("firebase_id", firebase_id).execute()

        if not user_result.data:
            return {
                "success": False,
                "message": "User not found"
            }

        db_user_id = user_result.data[0]["id"]

        # Check if face encoding exists
        encoding_result = supabase.table("face_encodings").select("id, created_at").eq("user_id", db_user_id).execute()

        if encoding_result.data:
            return {
                "success": True,
                "enrolled": True,
                "enrolled_at": encoding_result.data[0]["created_at"]
            }
        else:
            return {
                "success": True,
                "enrolled": False
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"Database error: {str(e)}"
        }



@app.post("/api/attendance")
async def mark_attendance(attendance_data: dict):
    """Mark attendance for a user"""
    if not SUPABASE_AVAILABLE:
        return {
            "success": False,
            "message": "Database not available"
        }

    try:
        firebase_id = attendance_data.get("firebase_id")
        status = attendance_data.get("status", "present")

        # Get user's database ID
        user_result = supabase.table("users").select("id").eq("firebase_id", firebase_id).execute()

        if not user_result.data:
            return {
                "success": False,
                "message": "User not found"
            }

        db_user_id = user_result.data[0]["id"]
        today = datetime.now().date().isoformat()

        # Check if attendance already marked today
        existing_result = supabase.table("attendance").select("*").eq("user_id", db_user_id).eq("date", today).execute()

        if existing_result.data:
            return {
                "success": False,
                "message": "Attendance already marked today"
            }

        # Mark attendance
        attendance_result = supabase.table("attendance").insert({
            "user_id": db_user_id,
            "date": today,
            "status": status,
            "timestamp": datetime.now().isoformat()
        }).execute()

        return {
            "success": True,
            "message": "Attendance marked successfully",
            "data": attendance_result.data[0] if attendance_result.data else None
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Database error: {str(e)}"
        }

@app.get("/api/attendance/{firebase_id}")
async def get_user_attendance(firebase_id: str, start_date: str = None, end_date: str = None):
    """Get attendance records for a user"""
    if not SUPABASE_AVAILABLE:
        return {
            "success": False,
            "message": "Database not available"
        }

    try:
        # Get user's database ID
        user_result = supabase.table("users").select("id").eq("firebase_id", firebase_id).execute()

        if not user_result.data:
            return {
                "success": False,
                "message": "User not found"
            }

        db_user_id = user_result.data[0]["id"]

        # Build query
        query = supabase.table("attendance").select("*").eq("user_id", db_user_id).order("date", desc=True)

        if start_date:
            query = query.gte("date", start_date)
        if end_date:
            query = query.lte("date", end_date)

        result = query.execute()

        return {
            "success": True,
            "data": result.data
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Database error: {str(e)}"
        }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "message": "Face Recognition API is running",
        "face_recognition_available": FACE_RECOGNITION_AVAILABLE,
        "supabase_available": SUPABASE_AVAILABLE
    }



@app.delete("/api/face-encodings/{firebase_id}")
async def delete_face_encoding(firebase_id: str):
    """Delete a user's face encoding (students only)"""
    if not SUPABASE_AVAILABLE:
        return {
            "success": False,
            "message": "Database not available"
        }

    try:
        # Get user's database ID and role
        user_result = supabase.table("users").select("id, role").eq("firebase_id", firebase_id).execute()

        if not user_result.data:
            return {
                "success": False,
                "message": "User not found"
            }

        user_data = user_result.data[0]
        db_user_id = user_data["id"]
        user_role = user_data["role"]

        # Check if user is a student (only students can have face encodings)
        if user_role != "student":
            return {
                "success": False,
                "message": "Face enrollment is only available for students."
            }

        # Delete face encoding (this will also delete the enrolled image)
        supabase.table("face_encodings").delete().eq("user_id", db_user_id).execute()

        # Remove profile photo for students (since their profile photo is the same as enrolled image)
        supabase.table("users").update({
            "profile_photo_url": None
        }).eq("firebase_id", firebase_id).execute()

        return {
            "success": True,
            "message": "Face encoding and enrolled image deleted successfully (profile photo also removed)"
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"Database error: {str(e)}"
        }

@app.post("/api/profile-photo/{firebase_id}")
async def save_profile_photo(firebase_id: str, image: UploadFile = File(...)):
    """Save profile photo for a user"""
    if not SUPABASE_AVAILABLE:
        return {
            "success": False,
            "message": "Database not available"
        }

    try:
        # Get user's database ID
        user_result = supabase.table("users").select("id").eq("firebase_id", firebase_id).execute()

        if not user_result.data:
            return {
                "success": False,
                "message": "User not found"
            }

        # Read and encode image as base64
        contents = await image.read()
        image_base64 = base64.b64encode(contents).decode('utf-8')

        # Create data URL
        content_type = image.content_type or 'image/jpeg'
        profile_photo_url = f"data:{content_type};base64,{image_base64}"

        # Update user's profile photo URL
        supabase.table("users").update({
            "profile_photo_url": profile_photo_url
        }).eq("firebase_id", firebase_id).execute()

        return {
            "success": True,
            "message": "Profile photo saved successfully",
            "profile_photo_url": profile_photo_url
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"Database error: {str(e)}"
        }

@app.put("/api/users/{firebase_id}/profile")
async def update_user_profile(firebase_id: str, profile_data: dict):
    """Update user profile information"""
    try:
        # Validate input data
        if not profile_data:
            return {"success": False, "message": "No profile data provided"}

        name = profile_data.get("name")
        student_id = profile_data.get("student_id")
        subject = profile_data.get("subject")

        # Validate required fields
        if not name or not isinstance(name, str) or len(name.strip()) == 0:
            return {"success": False, "message": "Name is required and cannot be empty"}

        name = name.strip()
        if len(name) < 2:
            return {"success": False, "message": "Name must be at least 2 characters long"}

        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": {"message": "Profile updated (demo mode)"},
                "message": "Profile updated successfully"
            }

        # Get user's current data to determine role
        try:
            user_result = supabase.table("users").select("id, role, student_id, subject").eq("firebase_id", firebase_id).execute()
        except Exception as db_error:
            return {"success": False, "message": "Database connection error. Please try again."}

        if not user_result.data:
            return {"success": False, "message": "User not found"}

        user_data = user_result.data[0]
        user_role = user_data["role"]

        # Prepare update data
        update_data = {
            "name": name,
            "updated_at": datetime.now().isoformat()
        }

        # Role-specific validation and updates
        if user_role == "student":
            if student_id is not None:
                if not isinstance(student_id, str) or len(student_id.strip()) == 0:
                    return {"success": False, "message": "Student ID cannot be empty"}

                student_id = student_id.strip()

                # Check if student ID is already taken by another user
                try:
                    existing_student = supabase.table("users").select("id").eq("student_id", student_id).neq("firebase_id", firebase_id).execute()
                except Exception as db_error:
                    return {"success": False, "message": "Error checking student ID availability. Please try again."}

                if existing_student.data:
                    return {"success": False, "message": "Student ID is already taken by another user"}

                update_data["student_id"] = student_id

        elif user_role == "teacher":
            if subject is not None:
                if not isinstance(subject, str) or len(subject.strip()) == 0:
                    return {"success": False, "message": "Subject cannot be empty"}

                subject = subject.strip()
                if len(subject) < 2:
                    return {"success": False, "message": "Subject must be at least 2 characters long"}

                update_data["subject"] = subject

        # Update user profile
        try:
            result = supabase.table("users").update(update_data).eq("firebase_id", firebase_id).execute()
        except Exception as db_error:
            return {"success": False, "message": "Failed to update profile. Please try again."}

        if result.data:
            return {
                "success": True,
                "data": result.data[0],
                "message": "Profile updated successfully"
            }
        else:
            return {"success": False, "message": "Failed to update profile"}

    except Exception as e:
        print(f"Unexpected error in update_user_profile: {str(e)}")
        return {
            "success": False,
            "message": "An unexpected error occurred. Please try again or contact support."
        }

@app.get("/api/profile-photo/{firebase_id}")
async def get_profile_photo(firebase_id: str):
    """Get profile photo for a user"""
    if not SUPABASE_AVAILABLE:
        return {
            "success": False,
            "message": "Database not available"
        }

    try:
        # Get user's profile photo URL
        user_result = supabase.table("users").select("profile_photo_url").eq("firebase_id", firebase_id).execute()

        if not user_result.data:
            return {
                "success": False,
                "message": "User not found"
            }

        profile_photo_url = user_result.data[0].get("profile_photo_url")

        if profile_photo_url:
            return {
                "success": True,
                "profile_photo_url": profile_photo_url
            }
        else:
            return {
                "success": True,
                "profile_photo_url": None,
                "message": "No profile photo found"
            }

    except Exception as e:
        return {
            "success": False,
            "message": f"Database error: {str(e)}"
        }

@app.get("/api/enrolled-image/{firebase_id}")
async def get_enrolled_image(firebase_id: str):
    """Get enrolled face image for a user"""
    if not SUPABASE_AVAILABLE:
        return {
            "success": False,
            "message": "Database not available"
        }

    try:
        # Get user's database ID
        user_result = supabase.table("users").select("id").eq("firebase_id", firebase_id).execute()

        if not user_result.data:
            return {
                "success": False,
                "message": "User not found"
            }

        db_user_id = user_result.data[0]["id"]

        # Get enrolled image from face_encodings table
        encoding_result = supabase.table("face_encodings").select("enrolled_image_url, created_at").eq("user_id", db_user_id).execute()

        if encoding_result.data and encoding_result.data[0].get("enrolled_image_url"):
            return {
                "success": True,
                "enrolled_image_url": encoding_result.data[0]["enrolled_image_url"],
                "enrolled_at": encoding_result.data[0]["created_at"]
            }
        else:
            return {
                "success": True,
                "enrolled_image_url": None,
                "message": "No enrolled image found"
            }

    except Exception as e:
        return {
            "success": False,
            "message": f"Database error: {str(e)}"
        }

@app.get("/api/all-enrolled-images")
async def get_all_enrolled_images():
    """Get all enrolled face images for teachers"""
    if not SUPABASE_AVAILABLE:
        return {
            "success": True,
            "enrolled_images": [
                {
                    "firebase_id": "demo123",
                    "name": "John Doe",
                    "email": "john@example.com",
                    "student_id": "STU001",
                    "enrolled_image_url": "https://via.placeholder.com/150",
                    "enrolled_at": "2024-01-01T00:00:00Z"
                }
            ]
        }

    try:
        # Get all enrolled images with user information
        result = supabase.table("face_encodings").select("""
            enrolled_image_url,
            created_at,
            users!inner (
                firebase_id,
                name,
                email,
                student_id,
                role
            )
        """).execute()

        enrolled_images = []
        for record in result.data or []:
            if record.get("enrolled_image_url") and record.get("users"):
                user_data = record["users"]
                if user_data.get("role") == "student":  # Only include students
                    enrolled_images.append({
                        "firebase_id": user_data["firebase_id"],
                        "name": user_data["name"],
                        "email": user_data["email"],
                        "student_id": user_data["student_id"],
                        "enrolled_image_url": record["enrolled_image_url"],
                        "enrolled_at": record["created_at"]
                    })

        return {
            "success": True,
            "enrolled_images": enrolled_images
        }

    except Exception as e:
        print(f"Error fetching enrolled images: {str(e)}")
        return {
            "success": False,
            "message": f"Database error: {str(e)}"
        }

# Class Management Endpoints

@app.post("/api/classes")
async def create_class(class_data: dict):
    """Create a new class"""
    try:
        name = class_data.get("name")
        subject = class_data.get("subject")
        description = class_data.get("description", "")
        teacher_firebase_id = class_data.get("teacher_firebase_id")

        if not name or not subject or not teacher_firebase_id:
            return {"success": False, "message": "Missing required fields"}

        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": {
                    "id": 1,
                    "name": name,
                    "subject": subject,
                    "class_code": None,
                    "description": description
                },
                "message": "Class created successfully (demo mode)"
            }

        # Get teacher's database ID
        teacher_result = supabase.table("users").select("id").eq("firebase_id", teacher_firebase_id).execute()
        if not teacher_result.data:
            return {"success": False, "message": "Teacher not found"}

        teacher_id = teacher_result.data[0]["id"]

        # Create class without requiring class code
        class_data_insert = {
            "name": name,
            "subject": subject,
            "description": description,
            "teacher_id": teacher_id,
            "class_code": None  # No class code required
        }

        result = supabase.table("classes").insert(class_data_insert).execute()

        if result.data:
            class_id = result.data[0]["id"]
            # Don't create automatic timetable - let teachers assign manually
            # try:
            #     supabase.rpc("create_default_timetable", {"class_id_param": class_id}).execute()
            # except Exception as e:
            #     print(f"Warning: Could not create default timetable: {str(e)}")
            print(f"Class created successfully. Teachers can now assign timetable slots manually.")

            # Remove class_code from response since we don't use it anymore
            response_data = result.data[0].copy()
            response_data.pop("class_code", None)

            return {
                "success": True,
                "data": response_data,
                "message": "Class created successfully"
            }
        else:
            return {"success": False, "message": "Failed to create class"}

    except Exception as e:
        print(f"Error creating class: {str(e)}")
        return {"success": False, "message": f"Failed to create class: {str(e)}"}

@app.get("/api/classes/teacher/{teacher_firebase_id}")
async def get_classes_by_teacher(teacher_firebase_id: str):
    """Get all classes for a teacher"""
    try:
        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": [
                    {
                        "id": 1,
                        "name": "Mathematics 101",
                        "subject": "Mathematics",
                        "class_code": "DEMO01",
                        "description": "Basic mathematics course",
                        "student_count": 25
                    }
                ]
            }

        # Get teacher's database ID
        teacher_result = supabase.table("users").select("id").eq("firebase_id", teacher_firebase_id).execute()
        if not teacher_result.data:
            return {"success": False, "message": "Teacher not found"}

        teacher_id = teacher_result.data[0]["id"]

        # Get classes
        result = supabase.table("classes").select("*").eq("teacher_id", teacher_id).execute()

        # Get student count for each class
        classes_with_count = []
        for class_data in result.data or []:
            enrollment_result = supabase.table("class_enrollments").select("id").eq("class_id", class_data["id"]).eq("status", "approved").execute()
            class_data["student_count"] = len(enrollment_result.data or [])
            classes_with_count.append(class_data)

        return {
            "success": True,
            "data": classes_with_count
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get classes: {str(e)}")

@app.get("/api/classes/student/{student_firebase_id}")
async def get_classes_by_student(student_firebase_id: str):
    """Get all classes for a student"""
    try:
        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": [
                    {
                        "id": 1,
                        "name": "Mathematics 101",
                        "subject": "Mathematics",
                        "class_code": "DEMO01",
                        "teacher_name": "John Doe",
                        "status": "approved"
                    }
                ]
            }

        # Get student's database ID
        student_result = supabase.table("users").select("id").eq("firebase_id", student_firebase_id).execute()
        if not student_result.data:
            return {"success": False, "message": "Student not found"}

        student_id = student_result.data[0]["id"]

        # Get enrolled classes with teacher info
        enrollments = supabase.table("class_enrollments").select("*").eq("student_id", student_id).execute()

        classes_data = []
        for enrollment in enrollments.data or []:
            class_result = supabase.table("classes").select("*").eq("id", enrollment["class_id"]).execute()
            if class_result.data:
                class_info = class_result.data[0]
                teacher_result = supabase.table("users").select("name").eq("id", class_info["teacher_id"]).execute()
                teacher_name = teacher_result.data[0]["name"] if teacher_result.data else "Unknown"

                classes_data.append({
                    "id": class_info["id"],
                    "name": class_info["name"],
                    "subject": class_info["subject"],
                    "class_code": class_info["class_code"],
                    "description": class_info["description"],
                    "teacher_name": teacher_name,
                    "status": enrollment["status"],
                    "enrolled_at": enrollment["enrolled_at"]
                })

        return {
            "success": True,
            "data": classes_data
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get student classes: {str(e)}")

@app.post("/api/classes/join")
async def join_class(join_data: dict):
    """Student joins a class directly (no approval needed)"""
    try:
        class_id = join_data.get("class_id")
        firebase_id = join_data.get("firebase_id")

        if not class_id or not firebase_id:
            return {"success": False, "message": "Class ID and Firebase ID are required"}

        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": {"message": "Joined class (demo mode)"},
                "message": "Joined class successfully"
            }

        # Get student's database ID
        student_result = supabase.table("users").select("id, role").eq("firebase_id", firebase_id).execute()
        if not student_result.data:
            return {"success": False, "message": "Student not found"}

        student_data = student_result.data[0]
        if student_data["role"] != "student":
            return {"success": False, "message": "Only students can join classes"}

        student_id = student_data["id"]

        # Verify class exists
        class_result = supabase.table("classes").select("id, name").eq("id", class_id).execute()
        if not class_result.data:
            return {"success": False, "message": "Class not found"}

        class_name = class_result.data[0]["name"]

        # Check if already enrolled
        existing_enrollment = supabase.table("class_enrollments").select("id, status").eq("class_id", class_id).eq("student_id", student_id).execute()
        if existing_enrollment.data:
            status = existing_enrollment.data[0]["status"]
            if status == "approved":
                return {"success": False, "message": "You are already enrolled in this class"}
            else:
                # Update existing enrollment to approved
                supabase.table("class_enrollments").update({
                    "status": "approved",
                    "approved_at": datetime.now().isoformat()
                }).eq("id", existing_enrollment.data[0]["id"]).execute()
                return {"success": True, "message": f"Successfully joined {class_name}"}

        # Create new enrollment with approved status (no approval needed)
        enrollment_data = {
            "class_id": class_id,
            "student_id": student_id,
            "status": "approved",
            "enrolled_at": datetime.now().isoformat(),
            "approved_at": datetime.now().isoformat()
        }

        result = supabase.table("class_enrollments").insert(enrollment_data).execute()

        if result.data:
            return {
                "success": True,
                "data": result.data[0],
                "message": f"Successfully joined {class_name}"
            }
        else:
            return {"success": False, "message": "Failed to join class"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to join class: {str(e)}")

@app.get("/api/classes/available")
async def get_available_classes():
    """Get all available classes for students to browse and join"""
    try:
        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": [
                    {
                        "id": 1,
                        "name": "Mathematics 101",
                        "subject": "Mathematics",
                        "description": "Basic mathematics course",
                        "teacher_name": "John Doe",
                        "enrolled_students": 25
                    },
                    {
                        "id": 2,
                        "name": "Physics 201",
                        "subject": "Physics",
                        "description": "Advanced physics concepts",
                        "teacher_name": "Jane Smith",
                        "enrolled_students": 18
                    }
                ]
            }

        # Get all classes with teacher info and enrollment count
        result = supabase.table("classes").select("""
            id,
            name,
            subject,
            description,
            created_at,
            users!classes_teacher_id_fkey (
                name
            )
        """).execute()

        classes_data = []
        for class_info in result.data or []:
            # Get enrollment count
            enrollment_result = supabase.table("class_enrollments").select("id").eq("class_id", class_info["id"]).eq("status", "approved").execute()
            enrollment_count = len(enrollment_result.data or [])

            teacher_name = "Unknown"
            if class_info.get("users"):
                teacher_name = class_info["users"]["name"]

            classes_data.append({
                "id": class_info["id"],
                "name": class_info["name"],
                "subject": class_info["subject"],
                "description": class_info["description"],
                "teacher_name": teacher_name,
                "enrolled_students": enrollment_count,
                "created_at": class_info["created_at"]
            })

        return {
            "success": True,
            "data": classes_data
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get available classes: {str(e)}")

@app.get("/api/classes/{class_id}/students")
async def get_class_students(class_id: int):
    """Get all students in a class"""
    try:
        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": [
                    {
                        "id": 1,
                        "name": "John Doe",
                        "email": "john@example.com",
                        "student_id": "STU001",
                        "status": "approved"
                    }
                ]
            }

        # Get enrollments with student info
        result = supabase.table("class_enrollments").select("""
            *,
            users!class_enrollments_student_id_fkey (
                id,
                name,
                email,
                student_id
            )
        """).eq("class_id", class_id).execute()

        students = []
        for enrollment in result.data or []:
            if enrollment.get("users"):
                user_data = enrollment["users"]
                students.append({
                    "id": user_data["id"],
                    "name": user_data["name"],
                    "email": user_data["email"],
                    "student_id": user_data["student_id"],
                    "status": enrollment["status"],
                    "enrolled_at": enrollment["enrolled_at"]
                })

        return {
            "success": True,
            "data": students
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get class students: {str(e)}")

@app.post("/api/classes/{class_id}/approve")
async def approve_student_join_request(class_id: int, approval_data: dict):
    """Approve a student's join request"""
    try:
        student_id = approval_data.get("student_id")
        teacher_firebase_id = approval_data.get("teacher_firebase_id")

        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": {"message": "Student approved (demo mode)"},
                "message": "Student approved successfully"
            }

        # Verify teacher owns the class
        teacher_result = supabase.table("users").select("id").eq("firebase_id", teacher_firebase_id).execute()
        if not teacher_result.data:
            return {"success": False, "message": "Teacher not found"}

        teacher_id = teacher_result.data[0]["id"]

        class_result = supabase.table("classes").select("id").eq("id", class_id).eq("teacher_id", teacher_id).execute()
        if not class_result.data:
            return {"success": False, "message": "Unauthorized or class not found"}

        # Update enrollment status
        result = supabase.table("class_enrollments").update({
            "status": "approved",
            "approved_at": datetime.now().isoformat(),
            "approved_by": teacher_id
        }).eq("class_id", class_id).eq("student_id", student_id).execute()

        if result.data:
            return {
                "success": True,
                "data": result.data[0],
                "message": "Student approved successfully"
            }
        else:
            return {"success": False, "message": "Failed to approve student"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to approve student: {str(e)}")

# Timetable Management Endpoints

@app.post("/api/timetables")
async def create_timetable(timetable_data: dict):
    """Create or update a timetable slot"""
    try:
        # Validate input data
        if not timetable_data:
            return {"success": False, "message": "No timetable data provided"}

        class_id = timetable_data.get("class_id")
        day_of_week = timetable_data.get("day_of_week")
        slot_number = timetable_data.get("slot_number")
        teacher_firebase_id = timetable_data.get("teacher_firebase_id")

        # Validate required fields
        if not class_id:
            return {"success": False, "message": "Class ID is required"}

        if not day_of_week:
            return {"success": False, "message": "Day of week is required"}

        if not slot_number:
            return {"success": False, "message": "Slot number is required"}

        if not teacher_firebase_id:
            return {"success": False, "message": "Teacher ID is required"}

        # Validate data types and ranges
        if not isinstance(class_id, int) or class_id <= 0:
            return {"success": False, "message": "Invalid class ID"}

        if not isinstance(day_of_week, int) or day_of_week < 1 or day_of_week > 7:
            return {"success": False, "message": "Day of week must be between 1 and 7"}

        if not isinstance(slot_number, int) or slot_number < 1 or slot_number > 9:
            return {"success": False, "message": "Slot number must be between 1 and 9"}

        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": {"message": "Timetable updated (demo mode)"},
                "message": "Timetable updated successfully"
            }

        # Verify teacher exists and owns the class
        try:
            teacher_result = supabase.table("users").select("id, role").eq("firebase_id", teacher_firebase_id).execute()
        except Exception as db_error:
            return {"success": False, "message": "Database connection error. Please try again."}

        if not teacher_result.data:
            return {"success": False, "message": "Teacher not found"}

        teacher_data = teacher_result.data[0]
        if teacher_data["role"] != "teacher":
            return {"success": False, "message": "Only teachers can manage timetables"}

        teacher_id = teacher_data["id"]

        # Verify teacher owns the class
        try:
            class_result = supabase.table("classes").select("id, name").eq("id", class_id).eq("teacher_id", teacher_id).execute()
        except Exception as db_error:
            return {"success": False, "message": "Error verifying class ownership. Please try again."}

        if not class_result.data:
            return {"success": False, "message": "Class not found or you don't have permission to modify this class"}

        class_name = class_result.data[0]["name"]

        # Check if slot already exists for this class
        try:
            existing_slot = supabase.table("timetable_slots").select("id").eq("class_id", class_id).eq("day_of_week", day_of_week).eq("slot_number", slot_number).execute()
        except Exception as db_error:
            return {"success": False, "message": "Error checking existing timetable slots. Please try again."}

        # Define time slots with 12-hour format
        time_slots = [
            ("09:00", "09:50"), ("09:50", "10:40"), ("10:50", "11:40"),
            ("11:40", "12:30"), ("12:30", "13:20"), ("13:20", "14:10"),
            ("14:10", "15:00"), ("15:10", "16:00"), ("16:00", "16:50")
        ]

        # Get the appropriate time slot
        if slot_number <= len(time_slots):
            start_time, end_time = time_slots[slot_number - 1]
        else:
            start_time, end_time = "09:00", "09:50"  # Default fallback

        if existing_slot.data:
            # Update existing slot with class information
            try:
                result = supabase.table("timetable_slots").update({
                    "start_time": start_time,
                    "end_time": end_time,
                    "class_id": class_id  # Ensure class_id is updated
                }).eq("id", existing_slot.data[0]["id"]).execute()
            except Exception as db_error:
                print(f"Database error updating slot: {db_error}")
                return {"success": False, "message": f"Failed to update timetable slot: {str(db_error)}"}
        else:
            # Create new slot with class information
            slot_data = {
                "class_id": class_id,
                "day_of_week": day_of_week,
                "slot_number": slot_number,
                "start_time": start_time,
                "end_time": end_time
            }
            try:
                result = supabase.table("timetable_slots").insert(slot_data).execute()
            except Exception as db_error:
                print(f"Database error creating slot: {db_error}")
                return {"success": False, "message": f"Failed to create timetable slot: {str(db_error)}"}

        if result.data:
            return {
                "success": True,
                "data": result.data[0],
                "message": f"Timetable updated successfully for {class_name}"
            }
        else:
            return {"success": False, "message": "Failed to update timetable"}

    except Exception as e:
        print(f"Unexpected error in create_timetable: {str(e)}")
        return {
            "success": False,
            "message": "An unexpected error occurred. Please try again or contact support."
        }

@app.get("/api/timetables/teacher/{teacher_firebase_id}")
async def get_timetable_by_teacher(teacher_firebase_id: str):
    """Get timetable for a teacher"""
    try:
        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": []
            }

        # Get teacher's database ID
        teacher_result = supabase.table("users").select("id").eq("firebase_id", teacher_firebase_id).execute()
        if not teacher_result.data:
            return {"success": False, "message": "Teacher not found"}

        teacher_id = teacher_result.data[0]["id"]

        # Get teacher's classes first
        teacher_classes = supabase.table("classes").select("id").eq("teacher_id", teacher_id).execute()
        teacher_class_ids = [cls["id"] for cls in teacher_classes.data or []]

        if not teacher_class_ids:
            return {
                "success": True,
                "data": []
            }

        # Get all timetable slots for teacher's classes with class details
        teacher_slots = []
        for class_id in teacher_class_ids:
            # Get class details
            class_result = supabase.table("classes").select("id, name, subject").eq("id", class_id).execute()
            if not class_result.data:
                continue

            class_info = class_result.data[0]

            # Get timetable slots for this class
            slots_result = supabase.table("timetable_slots").select("*").eq("class_id", class_id).execute()

            for slot in slots_result.data or []:
                teacher_slots.append({
                    "id": slot["id"],
                    "day_of_week": slot["day_of_week"],
                    "slot_number": slot["slot_number"],
                    "start_time": slot["start_time"],
                    "end_time": slot["end_time"],
                    "class": {
                        "id": class_info["id"],
                        "name": class_info["name"],
                        "subject": class_info["subject"]
                    }
                })

        return {
            "success": True,
            "data": teacher_slots
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get timetable: {str(e)}")

@app.get("/api/subjects")
async def get_all_subjects():
    """Get all unique subjects from users and classes tables"""
    try:
        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": ["Mathematics", "Physics", "Chemistry", "Biology", "Computer Science"]
            }

        # Get subjects from users table (teachers)
        users_result = supabase.table("users").select("subject").not_.is_("subject", "null").execute()

        # Get subjects from classes table
        classes_result = supabase.table("classes").select("subject").execute()

        subjects = set()

        # Add subjects from users
        for user in users_result.data or []:
            if user.get("subject"):
                subjects.add(user["subject"])

        # Add subjects from classes
        for class_item in classes_result.data or []:
            if class_item.get("subject"):
                subjects.add(class_item["subject"])

        return {
            "success": True,
            "data": sorted(list(subjects))
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get subjects: {str(e)}")

@app.post("/api/classes/add-students")
async def add_students_to_class(request_data: dict):
    """Add students to class by student ID range"""
    try:
        class_id = request_data.get("class_id")
        start_id = request_data.get("start_student_id")
        end_id = request_data.get("end_student_id")
        teacher_firebase_id = request_data.get("teacher_firebase_id")

        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": {"added_count": 5, "message": "Students added (demo mode)"},
                "message": "Students added successfully"
            }

        # Verify teacher owns the class
        teacher_result = supabase.table("users").select("id").eq("firebase_id", teacher_firebase_id).execute()
        if not teacher_result.data:
            return {"success": False, "message": "Teacher not found"}

        teacher_id = teacher_result.data[0]["id"]

        class_result = supabase.table("classes").select("id").eq("id", class_id).eq("teacher_id", teacher_id).execute()
        if not class_result.data:
            return {"success": False, "message": "Unauthorized or class not found"}

        # Find students in the ID range
        students_result = supabase.table("users").select("id, student_id, name").eq("role", "student").execute()

        eligible_students = []
        for student in students_result.data or []:
            student_id = student.get("student_id", "")
            if student_id and start_id <= student_id <= end_id:
                eligible_students.append(student)

        if not eligible_students:
            return {"success": False, "message": f"No students found with IDs between {start_id} and {end_id}"}

        # Add students to class with approved status
        added_count = 0
        for student in eligible_students:
            # Check if already enrolled
            existing = supabase.table("class_enrollments").select("id").eq("class_id", class_id).eq("student_id", student["id"]).execute()

            if not existing.data:
                enrollment_data = {
                    "class_id": class_id,
                    "student_id": student["id"],
                    "status": "approved",
                    "approved_at": datetime.now().isoformat(),
                    "approved_by": teacher_id
                }

                result = supabase.table("class_enrollments").insert(enrollment_data).execute()
                if result.data:
                    added_count += 1

        return {
            "success": True,
            "data": {
                "added_count": added_count,
                "total_eligible": len(eligible_students),
                "message": f"Added {added_count} students to the class"
            },
            "message": f"Successfully added {added_count} students to the class"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add students: {str(e)}")

# Instant Attendance System

# Store active instant passwords in memory (in production, use Redis)
instant_passwords = {}

@app.post("/api/instant-password/generate")
async def generate_instant_password(request_data: dict):
    """Generate instant password for class attendance"""
    try:
        class_id = request_data.get("class_id")
        slot_number = request_data.get("slot_number", 1)  # Default to slot 1
        teacher_firebase_id = request_data.get("teacher_firebase_id")

        if not SUPABASE_AVAILABLE:
            password = "123456"
            return {
                "success": True,
                "data": {
                    "password": password,
                    "expires_at": (datetime.now() + timedelta(minutes=3)).isoformat(),
                    "class_id": class_id
                },
                "message": "Instant password generated (demo mode)"
            }

        # Verify teacher owns the class
        teacher_result = supabase.table("users").select("id").eq("firebase_id", teacher_firebase_id).execute()
        if not teacher_result.data:
            return {"success": False, "message": "Teacher not found"}

        teacher_id = teacher_result.data[0]["id"]

        class_result = supabase.table("classes").select("id, name").eq("id", class_id).eq("teacher_id", teacher_id).execute()
        if not class_result.data:
            return {"success": False, "message": "Unauthorized or class not found"}

        # Generate 6-digit password
        import random
        password = str(random.randint(100000, 999999))
        expires_at = datetime.now() + timedelta(minutes=3)

        # Store in memory (in production, use Redis with TTL)
        instant_passwords[password] = {
            "class_id": class_id,
            "slot_number": slot_number,
            "teacher_id": teacher_id,
            "expires_at": expires_at,
            "created_at": datetime.now()
        }

        return {
            "success": True,
            "data": {
                "password": password,
                "expires_at": expires_at.isoformat(),
                "class_id": class_id,
                "class_name": class_result.data[0]["name"]
            },
            "message": "Instant password generated successfully"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate password: {str(e)}")

@app.post("/api/instant-password/invalidate")
async def invalidate_instant_password(request_data: dict):
    """Invalidate an instant password (for timer expiry or stop session)"""
    try:
        password = request_data.get("password")
        teacher_firebase_id = request_data.get("teacher_firebase_id")

        if not password:
            return {"success": False, "message": "Password is required"}

        if not teacher_firebase_id:
            return {"success": False, "message": "Teacher ID is required"}

        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "message": "Password invalidated (demo mode)"
            }

        # Check if password exists and belongs to the teacher
        if password in instant_passwords:
            password_data = instant_passwords[password]

            # Get teacher's database ID
            teacher_result = supabase.table("users").select("id").eq("firebase_id", teacher_firebase_id).execute()
            if teacher_result.data:
                teacher_id = teacher_result.data[0]["id"]

                # Verify the password belongs to this teacher
                if password_data["teacher_id"] == teacher_id:
                    del instant_passwords[password]
                    return {
                        "success": True,
                        "message": "Password invalidated successfully"
                    }
                else:
                    return {"success": False, "message": "Unauthorized to invalidate this password"}
            else:
                return {"success": False, "message": "Teacher not found"}
        else:
            return {
                "success": True,
                "message": "Password already expired or invalid"
            }

    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to invalidate password: {str(e)}"
        }

@app.post("/api/instant-attendance/validate")
async def validate_instant_password(request_data: dict):
    """Validate instant password without marking attendance"""
    try:
        # Validate input data
        if not request_data:
            return {"success": False, "message": "No data provided"}

        password = request_data.get("password")
        student_firebase_id = request_data.get("student_firebase_id")

        # Validate required fields
        if not password:
            return {"success": False, "message": "Password is required"}

        if not student_firebase_id:
            return {"success": False, "message": "Student ID is required"}

        # Validate password format
        if not isinstance(password, str) or len(password.strip()) == 0:
            return {"success": False, "message": "Invalid password format"}

        password = password.strip()

        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": {
                    "class_id": 1,
                    "class_name": "Demo Class",
                    "valid": True
                },
                "message": "Password validated (demo mode)"
            }

        # Check if password exists and is valid
        if password not in instant_passwords:
            return {"success": False, "message": "Invalid or expired password. Please check with your teacher."}

        password_data = instant_passwords[password]

        # Check if password has expired
        if datetime.now() > password_data["expires_at"]:
            # Clean up expired password
            try:
                del instant_passwords[password]
            except KeyError:
                pass  # Password already cleaned up
            return {"success": False, "message": "Password has expired during face recognition. Please ask your teacher for a new one."}

        # Get student info
        try:
            student_result = supabase.table("users").select("id, name").eq("firebase_id", student_firebase_id).execute()
        except Exception as db_error:
            return {"success": False, "message": "Error verifying student. Please try again."}

        if not student_result.data:
            return {"success": False, "message": "Student not found. Please contact support."}

        student_id = student_result.data[0]["id"]
        student_name = student_result.data[0]["name"]

        # Check if student is enrolled in the class
        try:
            enrollment_result = supabase.table("class_enrollments").select("status").eq("student_id", student_id).eq("class_id", password_data["class_id"]).execute()
        except Exception as db_error:
            return {"success": False, "message": "Error checking class enrollment. Please try again."}

        if not enrollment_result.data:
            # Auto-enroll student in the class if they have a valid password
            try:
                enrollment_data = {
                    "class_id": password_data["class_id"],
                    "student_id": student_id,
                    "status": "approved",
                    "enrolled_at": datetime.now().isoformat(),
                    "approved_at": datetime.now().isoformat()
                }
                supabase.table("class_enrollments").insert(enrollment_data).execute()
                print(f"Auto-enrolled student {student_id} in class {password_data['class_id']}")
            except Exception as enroll_error:
                print(f"Failed to auto-enroll student: {enroll_error}")
                return {"success": False, "message": "You are not enrolled in this class. Please join the class first from the 'Browse Classes' page."}
        elif enrollment_result.data[0]["status"] != "approved":
            # Auto-approve if they have a valid password
            try:
                supabase.table("class_enrollments").update({
                    "status": "approved",
                    "approved_at": datetime.now().isoformat()
                }).eq("id", enrollment_result.data[0]["id"]).execute()
                print(f"Auto-approved student {student_id} for class {password_data['class_id']}")
            except Exception as approve_error:
                print(f"Failed to auto-approve student: {approve_error}")
                return {"success": False, "message": "Your enrollment is pending teacher approval."}

        # Get class info
        try:
            class_result = supabase.table("classes").select("name").eq("id", password_data["class_id"]).execute()
        except Exception as db_error:
            return {"success": False, "message": "Error retrieving class information. Please try again."}

        class_name = class_result.data[0]["name"] if class_result.data else "Unknown Class"

        # Check if attendance already marked for this slot today
        today = datetime.now().date()
        slot_number = password_data["slot_number"]
        try:
            existing_attendance = supabase.table("attendance").select("id, created_at").eq("student_id", student_id).eq("class_id", password_data["class_id"]).eq("slot_number", slot_number).eq("attendance_date", today.isoformat()).execute()
        except Exception as db_error:
            return {"success": False, "message": "Error checking existing attendance. Please try again."}

        if existing_attendance.data:
            existing_time = datetime.fromisoformat(existing_attendance.data[0]["created_at"].replace('Z', '+00:00'))
            return {"success": False, "message": f"Attendance already marked for slot {slot_number} today at {existing_time.strftime('%I:%M %p')}"}

        return {
            "success": True,
            "data": {
                "class_id": password_data["class_id"],
                "class_name": class_name,
                "student_name": student_name,
                "valid": True
            },
            "message": "Password validated successfully. Please proceed with face recognition."
        }

    except Exception as e:
        print(f"Unexpected error in validate_instant_password: {str(e)}")
        return {
            "success": False,
            "message": "An unexpected error occurred. Please try again or contact support."
        }

@app.post("/api/instant-attendance/mark")
async def mark_instant_attendance(request_data: dict):
    """Mark attendance using instant password after face recognition"""
    try:
        # Validate input data
        if not request_data:
            return {"success": False, "message": "No data provided"}

        password = request_data.get("password")
        student_firebase_id = request_data.get("student_firebase_id")

        # Validate required fields
        if not password:
            return {"success": False, "message": "Password is required"}

        if not student_firebase_id:
            return {"success": False, "message": "Student ID is required"}

        # Validate password format
        if not isinstance(password, str) or len(password.strip()) == 0:
            return {"success": False, "message": "Invalid password format"}

        password = password.strip()

        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": {"message": "Attendance marked (demo mode)"},
                "message": "Attendance marked successfully"
            }

        # Check if password exists and is valid
        if password not in instant_passwords:
            return {"success": False, "message": "Invalid or expired password. Please check with your teacher."}

        password_data = instant_passwords[password]

        # Check if password has expired
        if datetime.now() > password_data["expires_at"]:
            # Clean up expired password
            try:
                del instant_passwords[password]
            except KeyError:
                pass  # Password already cleaned up
            return {"success": False, "message": "Password has expired. Please ask your teacher for a new one."}

        # Get student's database ID and validate user
        try:
            student_result = supabase.table("users").select("id, name, role").eq("firebase_id", student_firebase_id).execute()
        except Exception as db_error:
            return {"success": False, "message": "Database connection error. Please try again."}

        if not student_result.data:
            return {"success": False, "message": "Student account not found. Please contact your teacher."}

        student_data = student_result.data[0]
        if student_data["role"] != "student":
            return {"success": False, "message": "Only students can mark attendance using instant passwords"}

        student_id = student_data["id"]
        student_name = student_data["name"]

        # Check if student is enrolled in the class
        try:
            enrollment_result = supabase.table("class_enrollments").select("id, status").eq("class_id", password_data["class_id"]).eq("student_id", student_id).execute()
        except Exception as db_error:
            return {"success": False, "message": "Error checking class enrollment. Please try again."}

        if not enrollment_result.data:
            # Auto-enroll student in the class if they have a valid password
            try:
                enrollment_data_insert = {
                    "class_id": password_data["class_id"],
                    "student_id": student_id,
                    "status": "approved",
                    "enrolled_at": datetime.now().isoformat(),
                    "approved_at": datetime.now().isoformat()
                }
                supabase.table("class_enrollments").insert(enrollment_data_insert).execute()
                print(f"Auto-enrolled student {student_id} in class {password_data['class_id']} during attendance marking")
            except Exception as enroll_error:
                print(f"Failed to auto-enroll student during attendance: {enroll_error}")
                return {"success": False, "message": "You are not enrolled in this class. Please join the class first from the 'Browse Classes' page."}
        else:
            enrollment_data = enrollment_result.data[0]
            if enrollment_data.get("status") != "approved":
                # Auto-approve if they have a valid password
                try:
                    supabase.table("class_enrollments").update({
                        "status": "approved",
                        "approved_at": datetime.now().isoformat()
                    }).eq("id", enrollment_data["id"]).execute()
                    print(f"Auto-approved student {student_id} for class {password_data['class_id']} during attendance marking")
                except Exception as approve_error:
                    print(f"Failed to auto-approve student during attendance: {approve_error}")
                    return {"success": False, "message": "Your enrollment in this class is pending approval."}

        # Get current day of week and date
        today = datetime.now().date()
        day_of_week = datetime.now().weekday() + 1  # Convert to 1-7 format
        slot_number = password_data["slot_number"]

        # Check if attendance already marked for this slot today
        try:
            existing_attendance = supabase.table("attendance").select("id, created_at").eq("student_id", student_id).eq("class_id", password_data["class_id"]).eq("slot_number", slot_number).eq("attendance_date", today.isoformat()).execute()
        except Exception as db_error:
            return {"success": False, "message": "Error checking existing attendance. Please try again."}

        if existing_attendance.data:
            existing_time = datetime.fromisoformat(existing_attendance.data[0]["created_at"].replace('Z', '+00:00'))
            return {"success": False, "message": f"Attendance already marked for slot {slot_number} today at {existing_time.strftime('%I:%M %p')}"}

        # Mark attendance
        attendance_data = {
            "student_id": student_id,
            "class_id": password_data["class_id"],
            "slot_number": slot_number,
            "day_of_week": day_of_week,
            "attendance_date": today.isoformat(),
            "status": "present",
            "marked_by": "instant_password",
            "created_at": datetime.now().isoformat()
        }

        try:
            result = supabase.table("attendance").insert(attendance_data).execute()
        except Exception as db_error:
            return {"success": False, "message": "Failed to save attendance. Please try again."}

        if result.data:
            return {
                "success": True,
                "data": {
                    "student_name": student_name,
                    "status": "present",
                    "slot_number": slot_number,
                    "marked_at": datetime.now().isoformat()
                },
                "message": f"Attendance marked successfully for {student_name} (Slot {slot_number})"
            }
        else:
            return {"success": False, "message": "Failed to mark attendance"}

    except Exception as e:
        print(f"Unexpected error in mark_instant_attendance: {str(e)}")
        return {
            "success": False,
            "message": "An unexpected error occurred. Please try again or contact support."
        }

@app.post("/api/attendance/mark-manual")
async def mark_manual_attendance(request_data: dict):
    """Mark attendance manually by teacher"""
    try:
        student_firebase_id = request_data.get("student_firebase_id")
        class_id = request_data.get("class_id")
        slot_number = request_data.get("slot_number", 1)  # Default to slot 1 if not provided
        status = request_data.get("status", "present")
        teacher_firebase_id = request_data.get("teacher_firebase_id")

        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": {"message": "Attendance marked manually (demo mode)"},
                "message": "Attendance marked successfully"
            }

        # Verify teacher owns the class
        teacher_result = supabase.table("users").select("id").eq("firebase_id", teacher_firebase_id).execute()
        if not teacher_result.data:
            return {"success": False, "message": "Teacher not found"}

        teacher_id = teacher_result.data[0]["id"]

        class_result = supabase.table("classes").select("id").eq("id", class_id).eq("teacher_id", teacher_id).execute()
        if not class_result.data:
            return {"success": False, "message": "Unauthorized or class not found"}

        # Get student's database ID
        student_result = supabase.table("users").select("id, name").eq("firebase_id", student_firebase_id).execute()
        if not student_result.data:
            return {"success": False, "message": "Student not found"}

        student_id = student_result.data[0]["id"]
        student_name = student_result.data[0]["name"]

        # Get current day of week and date
        today = datetime.now().date()
        day_of_week = datetime.now().weekday() + 1  # Convert to 1-7 format

        # Check if attendance already marked for this slot today
        existing_attendance = supabase.table("attendance").select("id").eq("student_id", student_id).eq("class_id", class_id).eq("slot_number", slot_number).eq("attendance_date", today.isoformat()).execute()

        if existing_attendance.data:
            # Update existing attendance
            result = supabase.table("attendance").update({
                "status": status,
                "marked_by": "teacher",
                "updated_at": datetime.now().isoformat()
            }).eq("id", existing_attendance.data[0]["id"]).execute()
        else:
            # Create new attendance record
            attendance_data = {
                "student_id": student_id,
                "class_id": class_id,
                "slot_number": slot_number,
                "day_of_week": day_of_week,
                "attendance_date": today.isoformat(),
                "status": status,
                "marked_by": "teacher",
                "created_at": datetime.now().isoformat()
            }
            result = supabase.table("attendance").insert(attendance_data).execute()

        if result.data:
            return {
                "success": True,
                "data": {
                    "student_name": student_name,
                    "status": status,
                    "slot_number": slot_number,
                    "marked_at": datetime.now().isoformat()
                },
                "message": f"Attendance marked as {status} for {student_name} (Slot {slot_number})"
            }
        else:
            return {"success": False, "message": "Failed to mark attendance"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to mark attendance: {str(e)}")

@app.get("/api/attendance/check")
async def check_attendance(student_firebase_id: str, class_id: int, date: str, slot_number: int = None):
    """Check if attendance is already marked for a student in a class on a specific date and slot"""
    try:
        if not SUPABASE_AVAILABLE:
            return {
                "success": True,
                "data": None,
                "message": "No attendance found (demo mode)"
            }

        # Get student's database ID
        student_result = supabase.table("users").select("id").eq("firebase_id", student_firebase_id).execute()
        if not student_result.data:
            return {"success": False, "message": "Student not found"}

        student_id = student_result.data[0]["id"]

        # Check attendance for the specific date and slot
        query = supabase.table("attendance").select("*").eq("student_id", student_id).eq("class_id", class_id).eq("attendance_date", date)

        if slot_number is not None:
            query = query.eq("slot_number", slot_number)

        attendance_result = query.execute()

        if attendance_result.data:
            return {
                "success": True,
                "data": attendance_result.data[0],
                "message": "Attendance found"
            }
        else:
            return {
                "success": True,
                "data": None,
                "message": "No attendance found"
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to check attendance: {str(e)}")

@app.get("/api/current-slot")
async def get_current_slot():
    """Get the current time slot based on the current time"""
    try:
        current_time = datetime.now().time()
        current_day = datetime.now().weekday() + 1  # Convert to 1-7 format

        # Define slot times (9 slots from 9:00 AM to 5:00 PM)
        slot_times = [
            ("09:00", "09:50", 1),  # Slot 1
            ("09:50", "10:40", 2),  # Slot 2
            ("10:50", "11:40", 3),  # Slot 3 (after first recess)
            ("11:40", "12:30", 4),  # Slot 4
            ("12:30", "13:20", 5),  # Slot 5
            ("13:20", "14:10", 6),  # Slot 6 (after lunch break)
            ("14:10", "15:00", 7),  # Slot 7
            ("15:10", "16:00", 8),  # Slot 8 (after second recess)
            ("16:00", "16:50", 9),  # Slot 9
        ]

        current_slot = None
        for start_time_str, end_time_str, slot_num in slot_times:
            start_time = datetime.strptime(start_time_str, "%H:%M").time()
            end_time = datetime.strptime(end_time_str, "%H:%M").time()

            if start_time <= current_time <= end_time:
                current_slot = slot_num
                break

        return {
            "success": True,
            "data": {
                "current_slot": current_slot,
                "current_day": current_day,
                "current_time": current_time.strftime("%H:%M"),
                "slot_times": slot_times
            },
            "message": f"Current slot: {current_slot}" if current_slot else "No active slot at this time"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get current slot: {str(e)}")

if __name__ == "__main__":
    import uvicorn

    # Get port from environment variable (Render sets this automatically)
    port = int(os.environ.get("PORT", 8000))

    # Run the server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
