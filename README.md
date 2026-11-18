# YT Downloader (yt-dlp GUI)

תוכנה פשוטה להורדת סרטונים, פלייליסטים וערוצים שלמים בעזרת yt-dlp.

## שימוש
1. חלצו את ה-ZIP לתיקייה (הקבצים ffmpeg.exe ו-ffprobe.exe חייבים להישאר ליד YTDownloader.exe).
2. הפעילו את YTDownloader.exe.
3. הדביקו קישורים (סרטון / פלייליסט / ערוץ), שורה לכל קישור.
   לערוץ: https://www.youtube.com/@name/videos
4. בחרו תיקייה ואיכות ולחצו Download.

האפשרות "Skip files already downloaded" שומרת קובץ downloaded.txt בתיקיית היעד,
כך שהורדה חוזרת של אותו ערוץ תוריד רק סרטונים חדשים.

## בנייה אוטומטית ב-GitHub Actions
- כל push ל-main, הרצה ידנית (Actions > Run workflow), ובנייה שבועית - מייצרים ZIP תחת Artifacts.
- יצירת תג (למשל `git tag v1.0 && git push --tags`) מפרסמת את ה-ZIP גם ב-Releases.

## הרצה מקומית
    pip install -r requirements.txt
    python app.py

הורידו רק תוכן שיש לכם זכות להוריד, ובהתאם לתנאי השימוש של האתר.
