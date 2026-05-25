import { Button } from '@/components/ui/button'
import type { ReactNode, FormEvent } from 'react'

export function BottomCTA({
  children,
  disabled,
  onClick,
}: {
  children: ReactNode
  disabled?: boolean
  onClick?: (e: FormEvent) => void
}) {
  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 mx-auto max-w-lg consumer-dock border-t px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] lg:static lg:mt-2 lg:max-w-none lg:border lg:bg-transparent lg:p-0 lg:shadow-none lg:backdrop-blur-none">
      <Button
        type="button"
        disabled={disabled}
        onClick={e => onClick?.(e)}
        className="w-full h-14 rounded-xl text-[16px] font-extrabold app-primary-button border-0 lg:h-16 lg:text-[17px]"
      >
        {children}
      </Button>
    </div>
  )
}
