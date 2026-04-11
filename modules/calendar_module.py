import datetime
import os.path
from dateutil import parser as date_parser
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from config import config
from utils.logger import get_logger
from tts import speak

logger = get_logger("Calendar")
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), name="IST")

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

def get_calendar_service():
    creds_file = config.credentials.get("google_calendar_api", "credentials.json")
    if not os.path.exists(creds_file):
        logger.warning(f"Google Calendar credentials not found at {creds_file}")
        return None

    creds = None
    # The file token.json stores the user's access and refresh tokens
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        except Exception as e:
            logger.error(f"Error reading token.json: {e}")
            os.remove('token.json')
            creds = None
            
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                creds_file, SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except Exception as e:
        logger.error(f"Error building calendar service: {e}")
        return None

def fetch_todays_events():
    logger.info("Fetching today's events")
    service = get_calendar_service()
    if not service:
        return "I could not access your Google Calendar because credentials are not set up."

    try:
        # Query events for today's IST window.
        now_ist = datetime.datetime.now(IST)
        now = now_ist.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_of_day = now_ist.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
        
        events_result = service.events().list(calendarId='primary', timeMin=now,
                                              timeMax=end_of_day, maxResults=10, singleEvents=True,
                                              orderBy='startTime').execute()
        events = events_result.get('items', [])

        if not events:
            return "You have a free day."
        
        response = f"You have to do {len(events)} events today. "
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            # Simple time formatting
            if 'T' in start:
                start_dt = date_parser.parse(start).astimezone(IST)
                time_str = start_dt.strftime("%I:%M %p")
                response += f"{event['summary']} at {time_str}. "
            else:
                response += f"{event['summary']} today. "
                
        return response
    except Exception as e:
        logger.error(f"Error fetching schedule: {e}")
        return "There was an error retrieving your schedule."

def create_calendar_event(entity):
    logger.info(f"Creating calendar event from entity: {entity}")
    service = get_calendar_service()
    if not service:
        return "I could not access your Google Calendar because credentials are not set up."

    title = (entity or {}).get("title", "").strip()
    time_text = (entity or {}).get("time_text", "").strip()
    if not title or not time_text:
        return "I couldn't understand the event details. Please say something like schedule meeting at 6 PM."

    try:
        now = datetime.datetime.now(IST)
        start_dt = date_parser.parse(time_text, default=now.replace(hour=9, minute=0, second=0, microsecond=0))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=IST)
        else:
            start_dt = start_dt.astimezone(IST)
        end_dt = start_dt + datetime.timedelta(hours=1)

        event = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()}
        }
        created = service.events().insert(calendarId="primary", body=event).execute()
        logger.info(f"Created event: {created.get('id')}")
        spoken_time = start_dt.strftime("%I:%M %p")
        return f"Scheduled {title} at {spoken_time}."
    except Exception as e:
        logger.error(f"Error creating event: {e}")
        return "There was an error creating that calendar event."
