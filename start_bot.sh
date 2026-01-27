#!/bin/bash

echo "🧹 Stopping all old bot instances..."
pkill -9 -f "bot.py" 2>/dev/null
pkill -9 -f "run.py" 2>/dev/null
sleep 2

echo ""
echo "🚀 Starting bot with Mini App fixes..."
echo "📍 WEBAPP_URL: $(grep WEBAPP_URL .env | cut -d= -f2)"
echo ""

nohup python3 run.py > bot_output.log 2>&1 &
BOT_PID=$!

echo "✅ Bot started with PID: $BOT_PID"
echo ""
echo "📝 To view logs:"
echo "   tail -f bot_output.log"
echo ""
echo "🛑 To stop bot:"
echo "   kill $BOT_PID"
echo ""

sleep 3
echo "📊 Initial logs:"
tail -15 bot_output.log
