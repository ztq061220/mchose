import os, sys
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5.QtWidgets import QApplication
import app, battery
qapp=QApplication(sys.argv)
w=app.BatteryWidget()
w.refresh_timer.stop()
w._thread and w._thread.wait(1000)
status=battery.get_status()
w.apply_status(status)
w.show()
w.panel.show()
w._collapsed=False
w._recalc_size(); w._apply_layout(); w._ensure_expanded_visible()
qapp.processEvents()
print('root',w.size().width(),w.size().height())
print('panel',w.panel.size().width(),w.panel.size().height())
print('avatar',w.avatar.size().width(),w.avatar.size().height())
print('rows',len(w.panel.rows))
for r in w.panel.rows: print(' row',r.name.text(),r.status.text(),r.percent.text())
w.grab().save('preview_generic.png')
w.panel.grab().save('preview_generic_panel.png')
print('saved')
