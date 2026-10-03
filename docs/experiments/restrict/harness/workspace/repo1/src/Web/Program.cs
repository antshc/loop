// Disposable experiment app: serves one route, calls it once, prints the result, exits.
var builder = WebApplication.CreateBuilder(args);
builder.WebHost.UseUrls("http://127.0.0.1:5122");
var app = builder.Build();
app.MapGet("/hello", () => Greetings.Greeter.Greet(3) + " " + Clock.Stamp.Json());
await app.StartAsync();
using var http = new HttpClient();
Console.WriteLine("EXP22-RESULT: " + await http.GetStringAsync("http://127.0.0.1:5122/hello"));
await app.StopAsync();
