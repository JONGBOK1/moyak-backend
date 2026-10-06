import 'package:flutter/material.dart';
String? loadSession() => null;
void saveSession(String value) {}
void openPeer() {}
class DailyVideo extends StatelessWidget {
  const DailyVideo({super.key, required this.url});
  final String url;
  @override
  Widget build(BuildContext context) => const Center(child: Text('PC 브라우저에서 통화에 참여해주세요.'));
}
