using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Pipes;
using System.Net.WebSockets;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

internal sealed class ConnectionLink
{
    public Uri Site;
    public string Browser, Code;
    public static ConnectionLink Parse(string value)
    {
        if (value == null || value.Length > 4096) throw new ArgumentException("请从 Offerly 网站重新点击连接。");
        Uri uri;
        if (!Uri.TryCreate(value.Trim(), UriKind.Absolute, out uri) || uri.Scheme != "offerly-connect" || uri.Host != "connect" || uri.Fragment.Length != 0)
            throw new ArgumentException("连接链接无效，请回到 Offerly 网站重新连接。");
        var query = HttpUtility.ParseQueryString(uri.Query);
        Uri site;
        if (!Uri.TryCreate(query["site"], UriKind.Absolute, out site) ||
            (site.Scheme != "https" && !(site.Scheme == "http" && (site.Host == "localhost" || site.Host == "127.0.0.1"))) ||
            site.UserInfo.Length != 0 || site.AbsolutePath != "/" || site.Query.Length != 0 || site.Fragment.Length != 0 ||
            !Regex.IsMatch(query["code"] ?? "", @"\A[A-Za-z0-9_-]{43}\z") ||
            (query["browser"] != "edge" && query["browser"] != "chrome"))
            throw new ArgumentException("连接链接无效或已过期，请回网站重新点击连接。");
        return new ConnectionLink { Site = site, Browser = query["browser"], Code = query["code"] };
    }
}

internal static class Connector
{
    public static readonly string Home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Offerly", "Connect");
    public static readonly string PipeName = "OfferlyConnect-" + WindowsIdentity.GetCurrent().User.Value;
    public static readonly Color Ink = Color.FromArgb(38, 63, 53), Green = Color.FromArgb(49, 94, 76), Paper = Color.FromArgb(252, 250, 242), Muted = Color.FromArgb(117, 129, 117);

    [STAThread]
    private static int Main(string[] args)
    {
        System.Net.ServicePointManager.SecurityProtocol = System.Net.SecurityProtocolType.Tls12;
        if (args.Length == 1 && args[0] == "--version") return 0;
        if (args.Length == 3 && args[0] == "--verify")
        {
            try { Relay.Run(ConnectionLink.Parse(args[1]), new Uri(args[2]), CancellationToken.None, delegate { }).GetAwaiter().GetResult(); return 0; }
            catch (Exception error) {
                for (var item = error; item != null; item = item.InnerException)
                    Console.Error.WriteLine(item.GetType().Name + ": " + item.Message);
                return 1;
            }
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        if (args.Length == 1 && args[0] == "--preview") { Application.Run(new ConnectForm(null)); return 0; }
        try
        {
            string link = args.Length == 1 ? args[0] : null;
            if (link != null) ConnectionLink.Parse(link);
            Directory.CreateDirectory(Home);
            var installed = Path.Combine(Home, "OfferlyConnect.exe");
            if (!string.Equals(Application.ExecutablePath, installed, StringComparison.OrdinalIgnoreCase))
            {
                File.Copy(Application.ExecutablePath, installed, true);
                var start = new ProcessStartInfo(installed) { UseShellExecute = false };
                if (link != null) start.Arguments = "\"" + link.Replace("\"", "") + "\"";
                Process.Start(start);
                return 0;
            }
            using (var protocol = Registry.CurrentUser.CreateSubKey(@"Software\Classes\offerly-connect"))
            {
                protocol.SetValue("", "URL:Offerly 连接助手");
                protocol.SetValue("URL Protocol", "");
                using (var icon = protocol.CreateSubKey("DefaultIcon")) icon.SetValue("", "\"" + installed + "\",0");
                using (var command = protocol.CreateSubKey(@"shell\open\command")) command.SetValue("", "\"" + installed + "\" \"%1\"");
            }
            bool created;
            using (var mutex = new Mutex(true, "Local\\" + PipeName, out created))
            {
                if (!created)
                {
                    using (var pipe = new NamedPipeClientStream(".", PipeName, PipeDirection.Out))
                    {
                        pipe.Connect(5000);
                        using (var writer = new StreamWriter(pipe)) { writer.WriteLine(link ?? "show"); }
                    }
                    return 0;
                }
                var form = new ConnectForm(link);
                Task.Run(delegate { while (!form.IsDisposed) try {
                    using (var pipe = new NamedPipeServerStream(PipeName, PipeDirection.In, 1, PipeTransmissionMode.Byte, PipeOptions.Asynchronous)) {
                        pipe.WaitForConnection();
                        using (var reader = new StreamReader(pipe)) {
                            var message = reader.ReadLine();
                            if (message != null && message.Length <= 4096) form.BeginInvoke((Action)(() => form.Receive(message)));
                        }
                    }
                } catch { } });
                Application.Run(form);
            }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error is ArgumentException ? error.Message : "助手暂时无法启动。请关闭已有助手后重新打开；无需管理员权限。", "Offerly 连接助手", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 1;
        }
    }

    public static Uri BrowserEndpoint(string browser)
    {
        var profile = browser == "edge" ? @"Microsoft\Edge\User Data" : @"Google\Chrome\User Data";
        var path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), profile, "DevToolsActivePort");
        if (!File.Exists(path)) throw new ArgumentException("还差一步授权。点击下方“复制授权地址”，在浏览器地址栏粘贴打开，勾选允许远程调试，再点击连接。");
        var lines = File.ReadAllLines(path);
        int port;
        if (lines.Length < 2 || !int.TryParse(lines[0], out port) || port < 1 || port > 65535 || !Regex.IsMatch(lines[1], @"\A/devtools/browser/[A-Za-z0-9-]+\z"))
            throw new ArgumentException("请在浏览器设置页重新开启远程调试，然后再次连接。");
        return new Uri("ws://127.0.0.1:" + port + lines[1]);
    }
}

internal sealed class Card : Panel
{
    public Card() { DoubleBuffered = true; BackColor = Connector.Paper; }
    protected override void OnPaint(PaintEventArgs e) { base.OnPaint(e); using (var pen = new Pen(Color.FromArgb(222, 225, 211))) e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1); }
}

internal sealed class ConnectForm : Form
{
    private readonly Label state, detail, site, browser;
    private readonly TextBox fallback;
    private readonly Button connect, disconnect;
    private readonly NotifyIcon tray;
    private CancellationTokenSource stop;
    private string current;
    private bool running;
    public ConnectForm(string link)
    {
        Text = "Offerly · 连接助手"; ClientSize = new Size(600, 620); MinimumSize = new Size(616, 659);
        StartPosition = FormStartPosition.CenterScreen; BackColor = Color.FromArgb(238, 238, 228);
        Font = new Font("Microsoft YaHei UI", 10); ForeColor = Connector.Ink; AutoScaleMode = AutoScaleMode.Dpi;
        var brand = LabelAt("O.  Offerly", 32, 24, 530, 42, 24, true); Controls.Add(brand);
        Controls.Add(LabelAt("让浏览器，成为你的求职搭子。", 35, 77, 530, 27, 11, false));
        var card = new Card { Location = new Point(32, 125), Size = new Size(536, 263) }; Controls.Add(card);
        state = LabelAt("准备好了，等你连接", 24, 24, 488, 32, 16, true); card.Controls.Add(state);
        detail = LabelAt("助手已安装。回到 Offerly 网站，点击“连接我的浏览器”。", 24, 65, 488, 65, 10, false); card.Controls.Add(detail);
        site = LabelAt("网站   等待网站发起连接", 24, 139, 488, 27, 10, false); card.Controls.Add(site);
        browser = LabelAt("浏览器   使用网站中选择的 Edge 或 Chrome", 24, 171, 488, 27, 10, false); card.Controls.Add(browser);
        var copy = ActionButton("复制授权地址", 24, 212, 151, 34, false); card.Controls.Add(copy);
        copy.Click += delegate { string b = "edge"; try { b = ConnectionLink.Parse(current).Browser; } catch { } Clipboard.SetText(b + "://inspect/#remote-debugging"); SetState("授权地址已复制", "在浏览器地址栏粘贴打开，勾选 Allow remote debugging for this browser instance，然后回来连接。"); };
        connect = ActionButton("连接浏览器", 32, 406, 326, 48, true); Controls.Add(connect); connect.Click += async delegate { await Connect(); };
        disconnect = ActionButton("断开连接", 370, 406, 198, 48, false); disconnect.Enabled = false; Controls.Add(disconnect); disconnect.Click += delegate { if (stop != null) stop.Cancel(); };
        Controls.Add(LabelAt("没有自动唤起？粘贴网站上的连接链接", 33, 475, 533, 25, 9, false));
        fallback = new TextBox { Location = new Point(33, 505), Size = new Size(417, 29), BorderStyle = BorderStyle.FixedSingle, Font = new Font("Segoe UI", 10), MaxLength = 4096 }; Controls.Add(fallback);
        var paste = ActionButton("使用链接", 461, 502, 107, 34, false); Controls.Add(paste); paste.Click += delegate { Receive(fallback.Text); };
        Controls.Add(LabelAt("只连接你授权的浏览器 · 可随时断开\n连接期间请保持助手运行，最小化后仍可使用。", 34, 558, 532, 48, 9, false));
        tray = new NotifyIcon { Icon = SystemIcons.Application, Text = "Offerly 连接助手", Visible = true };
        tray.DoubleClick += delegate { Show(); WindowState = FormWindowState.Normal; Activate(); };
        var menu = new ContextMenuStrip(); menu.Items.Add("打开连接助手", null, delegate { Show(); WindowState = FormWindowState.Normal; Activate(); });
        menu.Items.Add("断开并退出", null, delegate { Close(); }); tray.ContextMenuStrip = menu;
        Resize += delegate { if (WindowState == FormWindowState.Minimized) Hide(); };
        FormClosed += delegate { if (stop != null) stop.Cancel(); tray.Dispose(); };
        Shown += delegate { if (link != null) Receive(link); };
    }
    private static Label LabelAt(string text, int x, int y, int w, int h, float size, bool bold)
    {
        return new Label { Text = text, Location = new Point(x, y), Size = new Size(w, h), Font = new Font("Microsoft YaHei UI", size, bold ? FontStyle.Bold : FontStyle.Regular), ForeColor = bold ? Connector.Ink : Connector.Muted, AutoEllipsis = true };
    }
    private static Button ActionButton(string text, int x, int y, int w, int h, bool primary)
    {
        var b = new Button { Text = text, Location = new Point(x, y), Size = new Size(w, h), FlatStyle = FlatStyle.Flat, BackColor = primary ? Connector.Green : Connector.Paper, ForeColor = primary ? Color.White : Connector.Green, Cursor = Cursors.Hand, UseVisualStyleBackColor = false };
        b.FlatAppearance.BorderColor = Color.FromArgb(205, 215, 199); return b;
    }
    private void SetState(string title, string message) { state.Text = title; detail.Text = message; }
    public async void Receive(string link)
    {
        Show(); WindowState = FormWindowState.Normal; Activate();
        if (link == "show") return;
        try {
            var parsed = ConnectionLink.Parse(link);
            if (running) { SetState("当前连接仍在运行", "请先断开当前连接，再回到网站重新点击连接。"); return; }
            current = link; site.Text = "网站   " + parsed.Site.Authority; browser.Text = "浏览器   " + (parsed.Browser == "edge" ? "Microsoft Edge" : "Google Chrome"); fallback.Clear();
            using (var settings = Registry.CurrentUser.CreateSubKey(@"Software\Offerly\Connect")) {
                if ((string)settings.GetValue("TrustedSite", "") == parsed.Site.GetLeftPart(UriPartial.Authority)) { await Connect(); return; }
            }
            SetState("允许 Offerly 连接你的浏览器", "请确认上方网站地址是你正在使用的 Offerly。点击“连接浏览器”后，在浏览器的授权提示里选择允许。");
        } catch (ArgumentException error) { SetState("链接需要更新", error.Message); }
    }
    private async Task Connect()
    {
        if (running) return;
        if (current == null) { SetState("请先回网站点击连接", "首次运行已经完成安装。回到 Offerly 网站点击“连接我的浏览器”，浏览器会自动打开此助手。"); return; }
        try {
            var link = ConnectionLink.Parse(current);
            var endpoint = Connector.BrowserEndpoint(link.Browser);
            using (var settings = Registry.CurrentUser.CreateSubKey(@"Software\Offerly\Connect")) settings.SetValue("TrustedSite", link.Site.GetLeftPart(UriPartial.Authority));
            running = true; connect.Enabled = false; disconnect.Enabled = true; stop = new CancellationTokenSource();
            await Relay.Run(link, endpoint, stop.Token, stage => {
                if (!IsDisposed) BeginInvoke((Action)(() => {
                    if (stage == "permission") SetState("请在浏览器里点击允许", "正在连接你已经打开的浏览器。首次连接需要浏览器授权，请勿关闭授权提示。");
                    else SetState("已连接，可以回到 Offerly 了", "Agent 将在独立投递标签页工作。你可以最小化助手，随时点击“断开连接”结束授权。");
                }));
            });
            SetState("连接已结束", "网页与已填写内容仍保留。需要继续时，请回到 Offerly 网站重新点击连接。");
        } catch (OperationCanceledException) { SetState("已断开连接", "你可以保留此窗口，下次直接从网站点击连接。"); }
        catch (Exception error) { SetState("暂时没有连接成功", error is ArgumentException ? error.Message : "请检查网络与浏览器授权。回到 Offerly 网站重新点击连接，获取新的连接链接。"); }
        finally { running = false; connect.Enabled = true; disconnect.Enabled = false; if (stop != null) stop.Dispose(); stop = null; }
    }
}

internal static class Relay
{
    private const int Limit = 32 * 1024 * 1024;
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = Limit, RecursionLimit = 256 };
    public static async Task Run(ConnectionLink link, Uri endpoint, CancellationToken cancel, Action<string> progress)
    {
        var temporary = Path.Combine(Connector.Home, "Sessions", Guid.NewGuid().ToString("N"));
        using (var local = new ClientWebSocket())
        using (var server = new ClientWebSocket())
        using (var stop = CancellationTokenSource.CreateLinkedTokenSource(cancel))
        using (var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancel))
        using (var send = new SemaphoreSlim(1, 1))
        {
            timeout.CancelAfter(TimeSpan.FromMinutes(10));
            var address = new UriBuilder(link.Site) { Scheme = link.Site.Scheme == "https" ? "wss" : "ws", Path = "/browser-helper/relay" };
            server.Options.SetRequestHeader("X-Offerly-Pair-Code", link.Code);
            server.Options.SetRequestHeader("X-Offerly-Browser", link.Browser);
            server.Options.KeepAliveInterval = TimeSpan.FromSeconds(20);
            try {
                await server.ConnectAsync(address.Uri, timeout.Token);
                progress("permission");
                await local.ConnectAsync(endpoint, timeout.Token);
                progress("connected");
                var up = BrowserToServer(local, server, send, stop.Token);
                var down = ServerToBrowser(server, local, send, temporary, stop.Token);
                var finished = await Task.WhenAny(up, down);
                stop.Cancel(); local.Abort(); server.Abort();
                try { await Task.WhenAll(up, down); } catch { }
                await finished;
            } finally {
                stop.Cancel(); local.Abort(); server.Abort();
                // Only this session's generated directory is removed, never user files.
                var root = Path.GetFullPath(Path.Combine(Connector.Home, "Sessions")) + Path.DirectorySeparatorChar;
                if (Path.GetFullPath(temporary).StartsWith(root, StringComparison.OrdinalIgnoreCase) && Directory.Exists(temporary))
                    try { Directory.Delete(temporary, true); } catch { }
            }
        }
    }
    private static async Task<byte[]> Read(ClientWebSocket socket, CancellationToken cancel)
    {
        var buffer = new byte[65536];
        using (var stream = new MemoryStream()) {
            while (true) {
                var message = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), cancel);
                if (message.MessageType == WebSocketMessageType.Close) return null;
                if (message.MessageType != WebSocketMessageType.Text || stream.Length + message.Count > Limit) throw new IOException("Message limit");
                stream.Write(buffer, 0, message.Count);
                if (message.EndOfMessage) return stream.ToArray();
            }
        }
    }
    private static async Task Send(ClientWebSocket socket, byte[] data, SemaphoreSlim gate, CancellationToken cancel)
    {
        await gate.WaitAsync(cancel);
        try { await socket.SendAsync(new ArraySegment<byte>(data), WebSocketMessageType.Text, true, cancel); }
        finally { gate.Release(); }
    }
    private static async Task BrowserToServer(ClientWebSocket local, ClientWebSocket server, SemaphoreSlim gate, CancellationToken cancel)
    {
        byte[] data;
        while ((data = await Read(local, cancel)) != null) await Send(server, data, gate, cancel);
    }
    private static async Task ServerToBrowser(ClientWebSocket server, ClientWebSocket local, SemaphoreSlim gate, string temporary, CancellationToken cancel)
    {
        byte[] data;
        while ((data = await Read(server, cancel)) != null) {
            var message = Json.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(data));
            object method;
            if (message.TryGetValue("method", out method) && (string)method == "Offerly.stageFile") {
                object response;
                try {
                    var p = (Dictionary<string, object>)message["params"];
                    var name = (string)p["name"];
                    if (string.IsNullOrWhiteSpace(name) || name.Length > 180 || name != Path.GetFileName(name) || name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || name.EndsWith(".") || name.EndsWith(" ")) throw new IOException("Filename");
                    var ext = Path.GetExtension(name).ToLowerInvariant();
                    if (Array.IndexOf(new [] {".pdf", ".jpg", ".jpeg", ".png", ".zip"}, ext) < 0) throw new IOException("File type");
                    var bytes = Convert.FromBase64String((string)p["content"]);
                    if (bytes.Length > 20 * 1024 * 1024) throw new IOException("File size");
                    var folder = Path.Combine(temporary, Guid.NewGuid().ToString("N")); Directory.CreateDirectory(folder);
                    var path = Path.Combine(folder, name); File.WriteAllBytes(path, bytes);
                    response = new { id = message["id"], result = new { path = path } };
                } catch { response = new { id = message["id"], error = new { code = -32000, message = "连接助手无法接收此附件，请检查文件名、类型与大小。" } }; }
                await Send(server, Encoding.UTF8.GetBytes(Json.Serialize(response)), gate, cancel);
            } else await local.SendAsync(new ArraySegment<byte>(data), WebSocketMessageType.Text, true, cancel);
        }
    }
}
