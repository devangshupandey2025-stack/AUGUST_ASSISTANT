import datetime
import os.path

from dateutil import parser as date_parser
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from august.config import config
from august.utils.logger import get_logger

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
    token_file = "token.json"
    # The file token.json stores the user's access and refresh tokens
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception as e:
            logger.error(f"Error reading token.json: {e}")
            _remove_stale_token(token_file)
            creds = None
            
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:
                logger.warning(f"Google Calendar token refresh failed; reauthorizing: {e}")
                _remove_stale_token(token_file)
                creds = _run_calendar_oauth_flow(creds_file)
        else:
            creds = _run_calendar_oauth_flow(creds_file)
        # Save the credentials for the next run
        with open(token_file, 'w') as token:
            token.write(creds.to_json())

    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except Exception as e:
        logger.error(f"Error building calendar service: {e}")
        return None


def _run_calendar_oauth_flow(creds_file):
    flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
    return flow.run_local_server(port=0, prompt="consent")


def _remove_stale_token(token_file):
    try:
        if os.path.exists(token_file):
            os.remove(token_file)
    except OSError as e:
        logger.warning(f"Could not remove stale Google Calendar token at {token_file}: {e}")

def fetch_todays_events(now: datetime.datetime | None = None, service=None):
    logger.info("Fetching today's events")
    service = service or get_calendar_service()
    if not service:
        return "I could not access your Google Calendar because credentials are not set up."

    try:
        # Query events from the current time through the end of today.
        now_ist = _as_ist(now or datetime.datetime.now(IST))
        start_time = now_ist.isoformat()
        end_of_day = now_ist.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
        
        events_result = service.events().list(calendarId='primary', timeMin=start_time,
                                              timeMax=end_of_day, maxResults=10, singleEvents=True,
                                              orderBy='startTime').execute()
        events = [_event for _event in events_result.get('items', []) if _should_announce_event(_event, now_ist)]

        if not events:
            return "You have no more events today."
        
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


def _as_ist(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def _should_announce_event(event, now_ist: datetime.datetime) -> bool:
    start = (event or {}).get('start', {})
    start_text = start.get('dateTime') or start.get('date')
    if not start_text:
        return False
    if 'dateTime' not in start:
        return True
    start_dt = _as_ist(date_parser.parse(start_text))
    return start_dt >= now_ist

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
