from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

router = Router(name="debug")

@router.message(F.text == "/debug_state")
async def cmd_debug_state(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    curr_state = await state.get_state()
    await message.reply(f"State: {curr_state}\nData: {data}")
