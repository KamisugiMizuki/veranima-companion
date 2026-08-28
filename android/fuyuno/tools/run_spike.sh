#!/usr/bin/env bash
# 冬乃 spike：构建 → 装到 MuMu test(#2) → 推送设备配置与角色卡
set -e
cd "$(dirname "$0")/.."
ADB="/c/Program Files/Netease/MuMuPlayer/nx_main/adb.exe"
S=127.0.0.1:16448
PKG=io.github.kamisugimizuki.veranima
PROJ=/d/Hermes_workspace/veranima
TMP=/data/local/tmp/fy

export JAVA_HOME="D:/Android-sdk/jdk-17.0.20.1+1"
/d/Android-sdk/gradle-8.13/bin/gradle.bat :app:assembleDebug

"$ADB" -s $S install -r -g app/build/outputs/apk/debug/app-debug.apk

python tools/gen_config.py "$PROJ" tools/device-config.yaml

"$ADB" -s $S shell "su 0 sh -c 'rm -rf $TMP; mkdir -p $TMP/characters/yuki'"
"$ADB" -s $S push tools/device-config.yaml "$TMP/config.yaml" >/dev/null
"$ADB" -s $S push "$PROJ/characters/yuki/character.json" "$TMP/characters/yuki/character.json" >/dev/null
"$ADB" -s $S push "$PROJ/characters/yuki/virtual_schedule.json" "$TMP/characters/yuki/virtual_schedule.json" >/dev/null
# 预放记忆备份（可选：存在则导入验证跨端记忆；spike 里只 push zip，导入靠 backup 逻辑后续接）
[ -f "$PROJ/data/veranima.db" ] && echo "（spike 使用全新空库；记忆包留到下一阶段）"
"$ADB" -s $S shell "su 0 sh -c '
  U=\$(stat -c %U /data/data/$PKG 2>/dev/null || echo u0_a52)
  mkdir -p /data/data/$PKG/files /data/data/$PKG/files/characters
  cp $TMP/config.yaml /data/data/$PKG/files/config.yaml
  cp -r $TMP/characters/* /data/data/$PKG/files/characters/
  chown -R \$U /data/data/$PKG/files
  chmod -R u+rwX /data/data/$PKG/files'"
echo "== push done =="
"$ADB" -s $S shell "am force-stop $PKG; am start -n $PKG/.MainActivity"
echo "== 启动完成 =="
