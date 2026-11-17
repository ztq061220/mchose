using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("桌面外设电量 安装程序")]
[assembly: AssemblyDescription("桌面外设电量安装程序")]
[assembly: AssemblyProduct("桌面外设电量")]
[assembly: AssemblyCompany("Desktop Battery Widget")]
[assembly: AssemblyVersion("1.6.2.0")]
[assembly: AssemblyFileVersion("1.6.2.0")]

namespace MchoseSetup
{
    static class Program
    {
        [DllImport("user32.dll")]
        static extern bool SetProcessDPIAware();

        [STAThread]
        static void Main(string[] args)
        {
            bool silent = false;
            string target = null;
            foreach (string a in args)
            {
                if (a == "/S" || a == "/s" || a == "/silent" || a == "/quiet" || a == "-s") silent = true;
                else if (a == "-n" || a == "/N") Installer.SkipShellIntegration = true;
                else if (a.StartsWith("/D=", StringComparison.OrdinalIgnoreCase) || a.StartsWith("-d=", StringComparison.OrdinalIgnoreCase))
                    target = a.Substring(3);
            }
            Installer.Log("Main args: " + string.Join(" | ", args));
            if (silent)
            {
                try
                {
                    Installer.Install(target, null);
                    Environment.Exit(0);
                }
                catch (Exception ex)
                {
                    Installer.Log("SILENT FAIL: " + ex);
                    Environment.Exit(1);
                }
            }
            SetProcessDPIAware();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupForm());
        }
    }

    static class Installer
    {
        public static readonly string DefaultTargetDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs", "MCHOSEBatteryWidget");
        public static string TargetDir = DefaultTargetDir;
        public static string ExePath { get { return Path.Combine(TargetDir, "MCHOSEBatteryWidget.exe"); } }
        public static string VbsPath { get { return Path.Combine(TargetDir, "launch-hidden.vbs"); } }
        public static string LogPath
        {
            get
            {
                string parent = Path.GetDirectoryName(TargetDir);
                if (string.IsNullOrEmpty(parent)) parent = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                return Path.Combine(parent, "MCHOSE_setup.log");
            }
        }
        public static bool SkipShellIntegration = false;

        public static void Log(string msg)
        {
            try { File.AppendAllText(LogPath, DateTime.Now.ToString("s") + " " + msg + "\r\n"); }
            catch { }
        }

        public static void Install(string targetDir, Action<int, string> report)
        {
            if (string.IsNullOrWhiteSpace(targetDir)) targetDir = DefaultTargetDir;
            TargetDir = Path.GetFullPath(targetDir);
            if (TargetDir.Length <= 3 || Path.GetPathRoot(TargetDir) == TargetDir) throw new Exception("不能安装到磁盘根目录");
            try { File.WriteAllText(LogPath, ""); } catch { }
            Log("install start target=" + TargetDir);
            Report(report, 4, "正在关闭旧版本...");
            KillOld();
            string parent = Path.GetDirectoryName(TargetDir);
            if (string.IsNullOrEmpty(parent)) throw new Exception("安装路径无效");
            Directory.CreateDirectory(parent);
            string temp = Path.Combine(parent, ".MCHOSEBatteryWidget_setup_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(temp);
            try
            {
                Report(report, 12, "正在解压安装文件...");
                string zip = Path.Combine(temp, "payload.zip");
                using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
                {
                    if (s == null) throw new Exception("找不到安装资源 payload.zip");
                    using (FileStream fs = File.Create(zip)) s.CopyTo(fs);
                }
                string extract = Path.Combine(temp, "app");
                Directory.CreateDirectory(extract);
                ZipFile.ExtractToDirectory(zip, extract);
                Log("extracted");

                Report(report, 40, "正在安装到当前用户目录...");
                if (Directory.Exists(TargetDir))
                {
                    try { Directory.Delete(TargetDir, true); }
                    catch
                    {
                        Thread.Sleep(600);
                        Directory.Delete(TargetDir, true);
                    }
                }
                Directory.CreateDirectory(TargetDir);
                CopyDirectory(extract, TargetDir);
                Log("copied");

                Report(report, 64, "正在创建启动器...");
                WriteVbs();

                if (!SkipShellIntegration)
                {
                    Report(report, 76, "正在创建快捷方式...");
                    try
                    {
                        CreateShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "桌面外设电量.lnk"));
                    }
                    catch (Exception ex)
                    {
                        Log("desktop shortcut warning: " + ex.Message);
                    }
                    try
                    {
                        CreateShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "桌面外设电量.lnk"));
                    }
                    catch (Exception ex)
                    {
                        Log("start menu shortcut warning: " + ex.Message);
                    }
                    Log("shortcuts");

                    Report(report, 88, "正在设置开机启动...");
                    try
                    {
                        SetStartup();
                        Log("startup");
                    }
                    catch (Exception ex)
                    {
                        Log("startup warning: " + ex.Message);
                    }
                }
                else
                {
                    Log("skip shell integration");
                }

                Report(report, 96, "正在启动挂件...");
                Process.Start(Path.Combine(Environment.SystemDirectory, "wscript.exe"), "\"" + VbsPath + "\"");
                Report(report, 100, "安装完成，已启动挂件。");
                Log("done");
            }
            finally
            {
                try { Directory.Delete(temp, true); } catch { }
            }
        }

        static void Report(Action<int, string> report, int value, string text)
        {
            if (report != null) report(value, text);
        }

        static void KillOld()
        {
            foreach (Process p in Process.GetProcessesByName("MCHOSEBatteryWidget"))
            {
                try
                {
                    p.Kill();
                    p.WaitForExit(3000);
                    Log("killed " + p.Id);
                }
                catch { }
            }
            Thread.Sleep(300);
        }

        static void CopyDirectory(string source, string target)
        {
            foreach (string dir in Directory.GetDirectories(source, "*", SearchOption.AllDirectories))
            {
                string rel = dir.Substring(source.Length).TrimStart(Path.DirectorySeparatorChar);
                Directory.CreateDirectory(Path.Combine(target, rel));
            }
            foreach (string file in Directory.GetFiles(source, "*", SearchOption.AllDirectories))
            {
                string rel = file.Substring(source.Length).TrimStart(Path.DirectorySeparatorChar);
                string dest = Path.Combine(target, rel);
                Directory.CreateDirectory(Path.GetDirectoryName(dest));
                File.Copy(file, dest, true);
            }
        }

        static void WriteVbs()
        {
            string txt = "Set sh = CreateObject(\"WScript.Shell\")\r\n"
                + "exe = \"" + ExePath + "\"\r\n"
                + "sh.Run \"\"\"\" & exe & \"\"\"\", 0, False\r\n";
            File.WriteAllText(VbsPath, txt, new System.Text.UTF8Encoding(false));
        }

        static void CreateShortcut(string path)
        {
            string wscript = Path.Combine(Environment.SystemDirectory, "wscript.exe");
            Type t = Type.GetTypeFromProgID("WScript.Shell");
            dynamic shell = Activator.CreateInstance(t);
            dynamic sc = shell.CreateShortcut(path);
            sc.TargetPath = wscript;
            sc.Arguments = "\"" + VbsPath + "\"";
            sc.WorkingDirectory = TargetDir;
            sc.IconLocation = ExePath + ",0";
            sc.Description = "桌面外设电量";
            sc.Save();
        }

        static void SetStartup()
        {
            string wscript = Path.Combine(Environment.SystemDirectory, "wscript.exe");
            using (RegistryKey k = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run"))
            {
                k.SetValue("MCHOSEBatteryWidget", "\"" + wscript + "\" \"" + VbsPath + "\"");
            }
        }
    }

    class SetupForm : Form
    {
        readonly Label status;
        readonly ProgressBar bar;
        readonly Button install;
        readonly Button cancel;
        readonly TextBox pathBox;
        readonly Button browse;
        bool installed;

        public SetupForm()
        {
            Text = "桌面外设电量 安装程序";
            ClientSize = new Size(520, 340);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            BackColor = Color.FromArgb(246, 247, 251);
            Font = new Font("Microsoft YaHei UI", 9F);
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

            Panel head = new Panel();
            head.Bounds = new Rectangle(0, 0, 520, 82);
            head.BackColor = Color.FromArgb(38, 53, 111);
        
            Label title = new Label();
            title.Text = "桌面外设电量";
            title.ForeColor = Color.White;
            title.Font = new Font("Microsoft YaHei UI", 20F, FontStyle.Bold);
            title.AutoSize = true;
            title.Location = new Point(24, 18);
            head.Controls.Add(title);

            Label sub = new Label();
            sub.Text = "v1.6.2  ·  桌面外设电量挂件";
            sub.ForeColor = Color.FromArgb(210, 218, 240);
            sub.Font = new Font("Microsoft YaHei UI", 9F);
            sub.AutoSize = true;
            sub.Location = new Point(26, 52);
            head.Controls.Add(sub);

            Label intro = new Label();
            intro.Text = "选择安装位置，安装后会创建桌面 / 开始菜单快捷方式。";
            intro.ForeColor = Color.FromArgb(55, 65, 81);
            intro.AutoSize = true;
            intro.Location = new Point(24, 100);
            Controls.Add(intro);

            Label where = new Label();
            where.Text = "安装位置（可修改）";
            where.ForeColor = Color.FromArgb(100, 110, 130);
            where.AutoSize = true;
            where.Location = new Point(24, 124);
            Controls.Add(where);

            pathBox = new TextBox();
            pathBox.Text = Installer.DefaultTargetDir;
            pathBox.Bounds = new Rectangle(24, 146, 380, 26);
            pathBox.Font = new Font("Microsoft YaHei UI", 9F);
            Controls.Add(pathBox);

            browse = new Button();
            browse.Text = "浏览...";
            browse.Bounds = new Rectangle(412, 145, 84, 28);
            browse.FlatStyle = FlatStyle.Flat;
            browse.FlatAppearance.BorderColor = Color.FromArgb(200, 208, 226);
            browse.BackColor = Color.White;
            browse.ForeColor = Color.FromArgb(55, 65, 81);
            browse.Click += BrowseClick;
            Controls.Add(browse);

            bar = new ProgressBar();
            bar.Bounds = new Rectangle(24, 190, 472, 8);
            bar.Minimum = 0;
            bar.Maximum = 100;
            Controls.Add(bar);

            status = new Label();
            status.Text = "准备安装";
            status.ForeColor = Color.FromArgb(100, 110, 130);
            status.AutoSize = true;
            status.Location = new Point(24, 206);
            Controls.Add(status);

            install = new Button();
            install.Text = "安装";
            install.Bounds = new Rectangle(284, 278, 110, 36);
            install.FlatStyle = FlatStyle.Flat;
            install.FlatAppearance.BorderSize = 0;
            install.BackColor = Color.FromArgb(38, 53, 111);
            install.ForeColor = Color.White;
            install.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Bold);
            install.Click += InstallClick;
            Controls.Add(install);

            cancel = new Button();
            cancel.Text = "取消";
            cancel.Bounds = new Rectangle(404, 278, 92, 36);
            cancel.FlatStyle = FlatStyle.Flat;
            cancel.FlatAppearance.BorderColor = Color.FromArgb(210, 216, 230);
            cancel.BackColor = Color.White;
            cancel.ForeColor = Color.FromArgb(70, 80, 100);
            cancel.Click += delegate { Close(); };
            Controls.Add(cancel);
        }
        void BrowseClick(object sender, EventArgs e)
        {
            using (FolderBrowserDialog dlg = new FolderBrowserDialog())
            {
                dlg.Description = "选择 桌面外设电量的安装文件夹";
                dlg.ShowNewFolderButton = true;
                if (Directory.Exists(pathBox.Text)) dlg.SelectedPath = pathBox.Text;
                if (dlg.ShowDialog(this) == DialogResult.OK) pathBox.Text = dlg.SelectedPath;
            }
        }

        void InstallClick(object sender, EventArgs e)
        {
            if (installed) { Close(); return; }
            install.Enabled = false;
            cancel.Enabled = false;
            Cursor = Cursors.WaitCursor;
            try
            {
                Installer.Install(pathBox.Text, delegate(int value, string text)
                {
                    bar.Value = Math.Max(0, Math.Min(100, value));
                    status.Text = text;
                    Application.DoEvents();
                });
                installed = true;
                pathBox.Enabled = false;
                browse.Enabled = false;
                status.Text = "安装完成，已启动挂件。";
                install.Text = "完成";
                install.Enabled = true;
                cancel.Visible = false;
            }
            catch (Exception ex)
            {
                Installer.Log("UI FAIL: " + ex);
                MessageBox.Show(this, "安装失败：\r\n" + ex.Message, "安装失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                status.Text = "安装失败";
                install.Enabled = true;
                cancel.Enabled = true;
            }
            finally { Cursor = Cursors.Default; }
        }
    }
}
