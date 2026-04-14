from django.core.management.base import BaseCommand
from attendance.utils import send_staff_reminder

class Command(BaseCommand):
    help = 'Sends 11:00 AM push notification reminders to teachers'

    def handle(self, *args, **kwargs):
        msg = "Daily Reminder: Please ensure your TOD and Class Attendance reports are submitted by 12:00 PM."
        result = send_staff_reminder(msg)
        
        if result.get('ok'):
            self.stdout.write(self.style.SUCCESS('Reminders sent successfully!'))
        else:
            self.stdout.write(
                self.style.ERROR(
                    f"Failed to send. Status: {result.get('status_code') or 'no response'}. {result.get('error', '')}"
                )
            )
