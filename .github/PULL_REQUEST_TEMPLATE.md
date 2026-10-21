## 变更说明

<!-- 为什么需要这个改动？解决了什么问题？ -->

## 类型

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / performance
- [ ] Docs / build / CI
- [ ] Security

## 验证

- [ ] `python -m unittest discover -s tests -v` 通过
- [ ] 手动验证：播放法语音频，控制台输出正常（或 asr 包只输出法语）
- [ ] 涉及便携包的改动已本地构建 `-Mode lite` 或 `-Mode asr`
- [ ] 新增/修改的用户提示为中文
- [ ] 未提交 `models/`、`dist/`、`portable/`、`.venv/`、音频文件或密钥

## 合规与安全

- [ ] 未新增上传音频/字幕或遥测
- [ ] 日志不包含识别/翻译正文
- [ ] 新增依赖/模型已更新 `THIRD_PARTY_NOTICES.md`，许可证与 MIT 兼容
- [ ] 无硬编码绝对路径、token、私钥

## 关联 Issue

Closes #
