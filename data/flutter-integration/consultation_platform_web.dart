import 'package:flutter/material.dart';
import 'package:web/web.dart' as web;

String? loadSession() => web.window.sessionStorage.getItem('moyak-flutter-session');
void saveSession(String value) => web.window.sessionStorage.setItem('moyak-flutter-session', value);
void openPeer() { web.window.open('/app/?role=pharmacist', 'moyak-flutter-pharmacist'); }

class DailyVideo extends StatelessWidget {
  const DailyVideo({super.key, required this.url});
  final String url;
  @override
  Widget build(BuildContext context) => HtmlElementView.fromTagName(
    tagName: 'iframe',
    onElementCreated: (element) {
      final frame = element as web.HTMLIFrameElement;
      frame.src = url;
      frame.title = 'Daily 영상통화';
      frame.allow = 'camera; microphone; fullscreen; display-capture; autoplay';
      frame.style..border = '0'..width = '100%'..height = '100%';
    },
  );
}
